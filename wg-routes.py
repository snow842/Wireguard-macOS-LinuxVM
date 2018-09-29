#!/usr/bin/env python
#
#  File: wg_routes.py
#
#  Version: 0.1
#
#  Purpose: Manipulate the routing table on macOS to enable or disable default
#           routing through a Wireguard tunnel running under a Linux VM.
#
#
#  Copyright (C) 2018 Michael Rash (mbr@cipherdyne.org)
#
#  License (GNU General Public License version 2 or any later version):
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02111-1301,
#  USA
#

from tempfile import NamedTemporaryFile
import socket
import re
import argparse
import sys, os

try:
    import subprocess32 as subprocess
except ImportError:
    import subprocess

__version__ = '0.1'

def main():

    config_file = os.path.expanduser('~/.wg-routes.conf')
    cmd  = ''
    conf = {}

    if len(sys.argv) == 2:
        cmd = sys.argv[1].lower()
    elif len(sys.argv) == 3:
        config_file = sys.argv[2]
    elif len(sys.argv) == 1:
        ### equate this with 'status'
        cmd = 'status'

    if cmd and '-' not in cmd:
        ### command mode, so validate the command and take the next steps
        if cmd != 'up' and cmd != 'down' and cmd != 'status':
            raise NameError("<cmd> must be one of up|down|status")

        if config_file:
            if not os.path.exists(config_file):
                raise NameError("config file '%s' does not exist" % config_file)

        parse_config(conf, config_file)

        if cmd == 'up' or cmd == 'down':
            ### now that we have the default gateway, the wireguard
            ### server, and the local VM IP's, add the routes
            route_update(cmd, conf)
            if cmd == 'up':
                up_guidance(conf['WG_CLIENT'])
            else:
                down_guidance(conf['WG_CLIENT'])
        else:
            route_status(conf)

        return 0

    ### we must be in --set, --list, or --version mode. Write the config based on
    ### command line arguments in --set mode.
    cargs = parse_cmdline()

    if cargs.version:
        print "wg-routes-" + __version__
        return 0

    if not cargs.set and not cargs.list:
        raise NameError("Must use one of --set or --list")

    if cargs.list:
        display_config(config_file, cargs)
        return 0

    if not cargs.wg_server:
        raise NameError("[*] Specify the Wireguard server IP (or hostname) with --wg-server")

    if not cargs.wg_client:
        raise NameError("[*] Specify the local VM IP/hostname where the Wireguard client is running with --wg-client")

    if cargs.config_file:
        config_file = cargs.config_file

    if cargs.set:
        ### write the config and exit
        write_config(config_file, cargs)
        print "Config written to '%s', now 'up|down|status' cmds can be used." \
                % config_file

    return 0

def parse_config(conf, config_file):
    with open(config_file, 'r') as f:
        for line in f:
            for var in ['WG_CLIENT', 'WG_SERVER', 'DEFAULT_GW']:
                m = re.search("^\s*%s\s+(\S+)" % var, line)
                if m:
                    ### resolve via DNS if necessary at parse time to allow hostnames
                    ### in the config
                    conf[var] = resolve(m.group(1))
                    break
    if 'DEFAULT_GW' not in conf:
        conf['DEFAULT_GW'] = get_default_gw()
    return

def display_config(config_file, cargs):
    print "\nDisplaying config: '%s'\n\n" % config_file
    with open(config_file, 'r') as f:
        for line in f:
            print line.rstrip()
    print
    return

def write_config(config_file, cargs):

    with open(config_file, 'w') as f:
        f.write('''#
# Configuration file for the '%s' utility
#

# The WG_CLIENT IP is usually a local VM running Wireguard
WG_CLIENT           %s

# The WG_SERVER IP is the remote Internet-connected system running Wireguard
WG_SERVER           %s

# Normally the default gateway is parsed from the local routing table and
# therefore does not need to be set here. It is only set if the --default-gw
# command line switch is used.
# DEFAULT_GW        __CHANGEME__
''' % (__file__, cargs.wg_client, cargs.wg_server))

        if cargs.default_gw:
            f.write("DEFAULT_GW          %s" % cargs.default_gw)
    return

def up_guidance(wg_client):
    print '''
With routing configured to send traffic to the Wireguard client system '%s',
it is usually necessary to add NAT rule in iptables along with allowing IP
forwarding. The NAT rule should translate incoming IP traffic from the Mac
to the Wireguard client IP assigned in the 'Address' line in the Wireguard
interface configuration file. The incoming traffic from the Mac is normally
the IP assigned to a virtual interface such as 'vnic0'. E.g.:

[wgclientvm]# iptables -t nat -A POSTROUTING -s <vnic0_IP> -j SNAT --to <WG_client_IP>

[wgclientvm]# echo 1 > /proc/sys/net/ipv4/ip_forward
''' % wg_client
    return

def down_guidance(wg_client):
    print '''
Applicable routes have been removed. The corresponding NAT rule and IP
forwarding configuration can be removed from the '%s' Wireguard client system.
''' % wg_client
    return

def route_status(conf):

    ### 0/1                10.111.55.31       UGSc           52        0   vnic0
    ### 128.0/1            10.111.55.31       UGSc            1        0   vnic0
    ### 2.2.2.2            192.168.0.1        UGHS            1       88     en

    netstat_cmd = 'netstat -rn'

    found_h1 = False
    found_h2 = False
    found_gw = False
    for line in run_cmd(netstat_cmd)[1]:
        if re.search("^\s*0\/1\s+%s\s" % conf['WG_CLIENT'], line):
            found_h1 = line.rstrip()
        elif re.search("^\s*128\.0\/1\s+%s\s" % conf['WG_CLIENT'], line):
            found_h2 = line.rstrip()
        elif re.search("^\s*%s\s+%s\s" % (conf['WG_SERVER'], conf['DEFAULT_GW']), line):
            found_gw = line.rstrip()

    if found_h1:
        print "Wireguard client route active: '%s'" % found_h1
    else:
        print "No Wireguard client route '0/1 -> %s'" % conf['WG_CLIENT']
    if found_h2:
        print "Wireguard client route active: '%s'" % found_h2
    else:
        print "No Wireguard client route '128.0/1 -> %s'" % conf['WG_CLIENT']
    if found_gw:
        print "Wireguard server route active: '%s'" % found_gw
    else:
        print "No Wireguard server route '%s -> %s'" % (conf['WG_SERVER'],
                conf['DEFAULT_GW'])
    return

def route_update(rcmd, conf):

    ### route add 0.0.0.0/1 <wg_client>
    ### route add 128.0.0.0/1 <wg_client>
    ### route add <wg_server> <default_gw>

    ### route add 0.0.0.0/1 10.111.55.31
    ### route add 128.0.0.0/1 10.111.55.31
    ### route add 2.2.2.2 192.168.0.1

    update_cmd = 'add'
    if rcmd == 'down':
        update_cmd = 'delete'

    print
    for cmd in ["route %s 0.0.0.0/1 %s" % (update_cmd, conf['WG_CLIENT']),
            "route %s 128.0.0.0/1 %s" % (update_cmd, conf['WG_CLIENT']),
            "route %s %s %s" % (update_cmd, conf['WG_SERVER'], conf['DEFAULT_GW'])
        ]:
        print "Running cmd: '%s'" % cmd
        (es, out) = run_cmd(cmd)

        ### look for indications of errors not caught by the process
        ### exit status
        found_err = False
        if rcmd == 'up':
            ### # route add 0.0.0.0/1 10.111.55.31
            ### route: writing to routing socket: File exists
            ### add net 0.0.0.0: gateway 10.111.55.31: File exists
            for line in out:
                if 'File exists' in line:
                    found_err = True
                    break
        elif rcmd == 'down':
            for line in out:
                ### # route delete 0.0.0.0/1 10.111.55.31
                ### route: writing to routing socket: not in table
                ### delete net 0.0.0.0: gateway 10.111.55.31: not in table
                if 'not in table' in line:
                    found_err = True
                    break
        if found_err:
            for line in out:
                print line

    return

def resolve(host):
    ip = ''
    if ':' in host:
        raise NameError("[*] IPv6 coming soon....")
    else:
        if re.search('(?:[0-2]?\d{1,2}\.){3}[0-2]?\d{1,2}', host):
            ip = host
        else:
            ### it's a hostname, so resolve
            ip = socket.gethostbyname(host)
            if not ip or not re.search('(?:[0-2]?\d{1,2}\.){3}[0-2]?\d{1,2}', ip):
                raise NameError("[*] Could not resolve '%s' to an IP" % ip)
    return ip

def get_default_gw():

    gw    = ''
    flags = ''
    netstat_cmd = 'netstat -rn'

    ### parse 'netstat -rn' output on macOS to get the default (IPv4) gw
    ### Destination        Gateway            Flags        Refs      Use   Netif Expire
    ### default            192.168.0.1        UGSc           69        0     en0

    for line in run_cmd(netstat_cmd)[1]:
        m = re.search('default\s+((?:[0-2]?\d{1,2}\.){3}[0-2]?\d{1,2})\s+(\w+)\s', line)
        if m:
            gw    = m.group(1)
            flags = m.group(2)
            break

    if gw and flags:
        for flag in ['G', 'U']:
            if flag not in flags:
                raise NameError(
                    "[*] Default gateway '%s' does not have the '%s' flag, set with --default-gw" \
                            % (gw, flag))
    else:
        raise NameError(
            "[*] Could not parse default gateway from '%s' output, set with --default-gw" \
                    % netstat_cmd)

    return gw

def run_cmd(cmd):
    out = []

    fh = NamedTemporaryFile(delete=False)
    es = subprocess.call(cmd, stdin=None,
            stdout=fh, stderr=subprocess.STDOUT, shell=True)
    fh.close()
    with open(fh.name, 'r') as f:
        for line in f:
            out.append(line.rstrip('\n'))
    os.unlink(fh.name)

    if (es != 0):
        print "[-] Non-zero exit status '%d' for CMD: '%s'" % (es, cmd)
        for line in out:
            print line

    return es, out

def parse_cmdline():
    p = argparse.ArgumentParser()

    p.add_argument("--wg-server", type=str,
            help="Set the Wireguard upstream server IP/hostname",
            default=False)
    p.add_argument("--wg-client", type=str,
            help="Set the local VM IP/hostname where the Wireguard client is running",
            default=False)
    p.add_argument("--default-gw", type=str,
            help="Manually set the IPv4 default gw (normally parsed from the routing table)",
            default=False)

    p.add_argument("--list", action='store_true',
            help="List the current configuration parameters", default=False)
    p.add_argument("--set", action='store_true',
            help="Write the --wg-server, --wg-client, and (optional) --default-gw to the config file",
            default=False)

    p.add_argument("-c", "--config-file", type=str,
            help="Specify the path to the config file (defaults to ~/.wg-routes.conf)",
            default=False)

    p.add_argument("-v", "--verbose", action='store_true',
            help="Verbose mode", default=False)
    p.add_argument("-V", "--version", action='store_true',
            help="Print version and exit", default=False)

    return p.parse_args()

if __name__ == "__main__":
    sys.exit(main())
