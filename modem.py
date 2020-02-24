#!/usr/bin/python3

# Copyright (c) 2020 James Grenier
#
# Permission is hereby granted, free of charge, to any person obtainng a copy
# of this software and associated documentation files (the "Software), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# modem.py
#
# This program configures a USB modem that takes a SIM card to communicate
# over a cellular network. Specifically, it has been tested with the
# Huawei Model E397u-53 HW Version B with a Google Fi SIM.
# An unlocked modem with the Cricket logo is available from Amazon.
#
# when executed with the "-c" option, it does the following:
#    - Opens a serial connection to the modem's control channel.
#    - Connects to the cellular network using DHCP.
#    - Gets the assigned IP address, netmask, gateway, and DNS server.
#    - Updates Linux with this information.
#
# when executed with the "-d" option, it does the following:
#    - Opens a serial connection to the modem's control channel.
#    - Disconnects from the cellular network.
#    - Removes the IP address, gateway, and DNS information from Linux
#
# This program assumes the following:
#    - The USM modem is already configured for modem operation.
#      (i.e. lsusb reports 12d1:1506)
#      If lsusb reports 12d1:1505, the modem is configured as mass store
#      for loading a Windows device driver. Use usb_modeswitch to change
#      the modem's configuration to act as a mode. See
#      draisberghof.de/usb_modeswitch for more details. You will have
#      to do something like:
#          - extract the configuration file with:
#              tar xf /usr/share/usb_modeswitch/configPack.tar.gz
#          - update the modem's mode with:
#              usb_modeswitch -v 12d1 -p 1505 -c 12d1\:1505
#    - The program is run with root permissions.
#    - The program is being executed on a Raspberry PI with the default
#      distribution. Commands used by the program need to change if using
#      a different distribution.
#    - If not using the Google Fi carrier, change the "carrier =" line
#      below to match your carrier's APN for the SIM.
#
# Issues:
#    - Trying to connect immediately after disconnecting can fail.
#      The modem takes time to reset.
#

import socket
import struct
import time
import serial
import subprocess
import re
import io
import sys
import argparse

# Script Configuration Parameters
dev      = 'wwan0'            # ifconfig modem device
dev_at   = '/dev/ttyUSB0'     # serial port for AT commands
ifconfig = '/sbin/ifconfig'   # command to query for interface status
carrier  = 'h2g2'             # Google Fi = h2g2
verbose  = 0                  # 0 - none, 1 - errors, 2 = all

###############################################################
# FUNCTION: OpenModem
#
# PARAMETERS:
#    device: modem's serial interface under /dev
#
# open a connection to the serial interface used to manage the modem
###############################################################
def OpenModem(device):
    try:
        ser = serial.Serial(device, timeout=2)   # open port to modem
    except serial.SerialException:
        if verbose >= 1:
            print('Failed to open serial port')
        return None
    return ser
####

###############################################################
# FUNCTION: GetInterface
#
# PARAMETERS:
#    device: modem's ifconfig name
#
# Returns output of ifconfig for cellular modem.
# This contains its IP address, if it has one.
#
###############################################################
def GetInterface(device):
    try:
        interface = subprocess.check_output((ifconfig, device),
                                  stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print(device + ': Failed to find interface using ' + \
                  ifconfig + ', check modem')
        return None
    return interface

###############################################################
# FUNCTION: GetIpAddr
#
# PARAMETERS:
#    if: output of ifconfig command
#
# Parses the IP address from the ifconfig output
###############################################################
def GetIpAddr(ifout):
    modem_addr = re.search(r'inet ([0-9.]*)', ifout.decode('utf-8'))
    if modem_addr:
        return modem_addr.group(1)
    return None
####

###############################################################
# FUNCTION: GetIpMask
#
# PARAMETERS:
#    if: output of ifconfig command
#
# Parses the IP address from the ifconfig output
###############################################################
def GetIpMask(ifout):
    modem_netmask = re.search(r'netmask ([0-9.]*)', ifout.decode('utf-8'))
    if modem_netmask:
        return modem_netmask.group(1)
    return None
####

###############################################################
# FUNCTION: ModemOk
#
# PARAMETERS:
#    ser: serial interface to cellular modem
#
# Test if modem is working. Returns true if okay.
###############################################################
def ModemOk(ser):
    ser.write(str.encode('AT\r'))
    s2 = ser.read(10)
    if s2:
        s = s2.decode("utf-8")
    if verbose >= 2:
        print('ModemOk:' + s)
    ok = re.search('OK', s)
    if ok is None:
        return False
    return True
####

###############################################################
# FUNCTION: ModemHangup
#
# PARAMETERS:
#    ser: serial interface to cellular modem
#
# Drop the cellular connection
###############################################################
def ModemHangup(ser):
    ser.write(str.encode('AT^NDISDUP=1,0\r'))
    s2 = ser.read(20)
    if s2:
        s = s2.decode("utf-8")
    if verbose >= 2:
        print('ModemHangup:' + s)
    ok = re.search('OK', s)
    if ok is None:
        err = re.search('ERROR', s)
        if err is None:
            t = ser.timeout
            ser.timeout = 20  # give it time to hangup
            s2 = ser.read(10)
            if s2:
                s = s2.decode("utf-8")
            ser.timeout = t
            ok = re.search('OK', s)
            if ok is None:
                if verbose >= 1:
                    print('ModemHangup Error:' + s)
                return False
            if verbose >= 2:
                print('ModemHangup Delayed Response:' + s)
        else:
            if verbose >= 1:
                print('ModemHangup Error:' + s)
            return False
    return True
####

###############################################################
# FUNCTION: ModemConnect
#
# PARAMETERS:
#    ser: serial interface to cellular modem
#
# Connect over the cellular modem
###############################################################
def ModemConnect(ser):
    ser.write(str.encode('AT^NDISDUP=1,1,"' + carrier + '"\r'))
    s2 = ser.read(30)
    if s2:
        s = s2.decode("utf-8")
    if verbose >= 2:
        print('ModemConnect:' + s)
    ok = re.search('OK', s)
    if ok is None:
        if verbose >= 1:
            print('ModemConnect Error:' + s)
        return False
    return True
####

###############################################################
# FUNCTION: DhcpParse
#
# PARAMETERS:
#    dhcp: response to DHCP query
#
# returns dictionary of parsed fields
#         IpAddr, Netmask, Gateway, DhcpServer, DnsPrimary,
#         DnsSecondary, RxMaxBps, TxMaxBps, NetmaskBits
###############################################################
def DhcpParse(dhcp):
    ips = []
    if isinstance(dhcp, str):
        fields = dhcp.split(':',1)
        if len(fields) == 2:
            field_array = fields[1].split(',')
            if len(field_array) >= 8:
                for i in range(6):
                    ips.append(socket.inet_ntoa(struct.pack("<L",
                                         int(field_array[i], base=16))))
                netmaskbits = sum([bin(int(x)).count('1') \
                                     for x in ips[1].split('.')])
                txMaxBps = field_array[7].split()
                return { 'IpAddr'       : ips[0],
                         'Netmask'      : ips[1],
                         'Gateway'      : ips[2],
                         'DhcpServer'   : ips[3],
                         'DnsPrimary'   : ips[4],
                         'DnsSecondary' : ips[5],
                         'RxMaxBps'     : field_array[6],
                         'TxMaxBps'     : txMaxBps[0],
                         'NetmaskBits'  : netmaskbits }
    return None
####

###############################################################
# FUNCTION: ModemDhcpStatus
#
# PARAMETERS:
#    ser: serial interface to cellular modem
#
# returns description of DHCP status
###############################################################
def ModemDhcpStatus(ser):
    ser.write(str.encode('AT^DHCP?\r'))
    s2 = ser.read(100)
    if s2:
        s = s2.decode("utf-8")
    if verbose >= 2:
        print('ModemDhcpStatus:' + s)
    ok = re.search('OK', s)
    if ok is None:
        return None
    return DhcpParse(s)
####

###############################################################
# FUNCTION: EraseInterfaceIpAddress
#
# PARAMETERS:
#    dev       : ifconfig device name for modem
#
# Remove the IP address from the ifconfig status report
###############################################################
def EraseInterfaceIpAddress(dev):
    ifStatus = GetInterface(dev)
    if ifStatus is not None:
        # delete the old IP address
        subprocess.call('/sbin/ip addr flush dev {}'.format(dev),
                shell=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        if verbose >= 2:
            print('Erased {} IP address'.format(dev))
####

###############################################################
# FUNCTION: UpdateInterfaceIpAddress
#
# PARAMETERS:
#    dev       : ifconfig device name for modem
#    dhcpStatus: dictionary of DHCP status from modem
#
# Update the system's IP address to match the IP address
# discovered by the modem.
###############################################################
def UpdateInterfaceIpAddress(dev, dhcpStatus):
    if dhcpStatus is None:
        return
    ifStatus = GetInterface(dev)
    if ifStatus is not None:
        addr = GetIpAddr(ifStatus)
        netmask = GetIpMask(ifStatus)
        if (addr != dhcpStatus['IpAddr']) or \
           (netmask != dhcpStatus['Netmask']):
            # delete the old IP address
            subprocess.call('/sbin/ip addr flush dev {}'.format(dev),
                    shell=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            # assign the new IP address
            subprocess.call('/sbin/ip address add {}/{} dev {}'.format(
                      dhcpStatus['IpAddr'], dhcpStatus['NetmaskBits'], dev),
                      shell=True, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
            if verbose >= 2:
                print('Setup {} IP address to {}/{}'.format(
                      dev, dhcpStatus['IpAddr'], dhcpStatus['NetmaskBits']))
####

###############################################################
# FUNCTION: GetGatewayInfo
#
# PARAMETERS:
#    dev       : ifconfig device name for modem
#
# Returns dictionary containing:
#     IpAddr: Gateway IP address
#     Device: Gateway ifconfig interface device
###############################################################
def GetGatewayInfo(dev):
    # get the current default gateway
    if dev:
        try:
            p1 = subprocess.Popen(["/sbin/route"], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(["/bin/grep", " " + dev + "$"],
                            stdin=p1.stdout, stdout=subprocess.PIPE)
            p1.stdout.close()
            r = p2.communicate()
            routes = r[0]
        except subprocess.CalledProcessError:
            if verbose >= 1:
                print(device + ': Failed to query for gateway')
            return None
    else:
        try:
            routes = subprocess.check_output(('/sbin/route'),
                                      stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError:
            if verbose >= 1:
                print(device + ': Failed to query for gateway')
            return None

    default = re.search(r'default *(.*)', routes.decode('utf-8'))
    if default:
        # get the current gateway
        gateway = re.match(r'([0-9.]*)', default.group(1))
        if verbose >= 2:
            print('Current gateway: ' + gateway.group(1))

        # get the gateway device
        index = default.group(1).rfind(' ')
        gateway_dev = default.group(1)[index+1:]
        if verbose >= 2:
            print('Current gateway device: ' + gateway_dev)
        return { 'IpAddr': gateway.group(1), 'Device': gateway_dev }
    return None
####

###############################################################
# FUNCTION: RemoveGateway
#
# PARAMETERS:
#    gateway : Gateway IP address
#    dev     : ifconfig device name
#
# Remove the gateway associated with the device.
###############################################################
def RemoveGateway(gateway, dev):
    try:
        subprocess.call('/sbin/route delete default gw {} {}'.format(
                gateway, dev), shell=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        if verbose >= 2:
            print('RemoveGateway: deleted gateway to {} {}'.format(
                   gateway, dev))
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print('RemoveGateway: Failed to delete gateway')
####

###############################################################
# FUNCTION: SetGateway
#
# PARAMETERS:
#    gateway : Gateway IP address
#    dev     : ifconfig device name
#
# Add the modem's gateway to the route table.
###############################################################
def SetGateway(gateway, dev):
    currentGateway = GetGatewayInfo(dev)
    if currentGateway is not None:
        if currentGateway['IpAddr'] == gateway:
            if verbose >= 2:
                print('SetGateway: gateway already set to ' + gateway)
            return

        # delete the old gateway
        RemoveGateway(currentGateway['IpAddr'], currentGateway['Device']);

    # set the new gateway
    try:
        subprocess.call("/sbin/ip route add default via {}".format(gateway),
                shell=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        if verbose >= 2:
            print('Setup new gateway to {}'.format(gateway))
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print('SetGateway: Failed to setup gateway')
####

###############################################################
# FUNCTION: AddDns
#
# PARAMETERS:
#    dns : DNS IP address
#    dev : ifconfig device name
#
# Add the modem's DNS
###############################################################
def AddDns(dns, dev):
    try:
        p1 = subprocess.Popen(["/bin/echo", "nameserver " + dns],
                              stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["/sbin/resolvconf", "-a", dev + ".modem"],
                        stdin=p1.stdout, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        p1.stdout.close()
        if verbose >= 2:
            print('AddDns: added DNS {} for {}'.format(dns, dev))
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print(dev + ': Failed to add DNS IP address')
####

###############################################################
# FUNCTION: RmDns
#
# PARAMETERS:
#    dev : ifconfig device name
#
# Remove the modem's DNS
###############################################################
def RmDns(dev):
    try:
        subprocess.call('/sbin/resolvconf -d {}.modem'.format(dev),
                shell=True, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        if verbose >= 2:
            print('RmDns: removed DNS for {}'.format(dev))
    except subprocess.CalledProcessError:
        if verbose >= 1:
            print('RmDns: Failed to remove DNS')
####


###############################################################
##
## Main Program
##
###############################################################

### Parse command line
parser = argparse.ArgumentParser()
parser.add_argument("-c", "--connect",
                    action='count',
                    help="connect modem to network")
parser.add_argument("-d", "--disconnect",
                    action='count',
                    help="disconnect modem from network")
parser.add_argument("-v", "--verbosity",
                    action='count',
                    help="increase output verbosity")
args = parser.parse_args()
if args.verbosity:
    verbose = args.verbosity

if args.connect:
    if verbose >= 2:
        print('Connecting with modem')
    ser = OpenModem(dev_at)
    if ser:
        ok = ModemOk(ser)
        ok = ModemConnect(ser)
        status = ModemDhcpStatus(ser)
        if status:
            UpdateInterfaceIpAddress(dev, status)
            SetGateway(status['Gateway'], dev)
            AddDns(status['DnsPrimary'], dev)

if args.disconnect:
    ser = OpenModem(dev_at)
    ok = ModemHangup(ser)
    EraseInterfaceIpAddress(dev)
    currentGateway = GetGatewayInfo(dev)
    if currentGateway is not None:
        RemoveGateway(currentGateway['IpAddr'], dev)
    RmDns(dev)

if not args.connect and not args.disconnect:
    parser.print_help()

exit(0)


