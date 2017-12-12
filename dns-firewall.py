#!/usr/bin/python
# -*- coding: utf-8 -*-

'''
=================================================================================
dns-firewall.py: v2.00 Copyright (C) 2017 Chris Buijs <cbuijs@chrisbuijs.com>
=================================================================================

Based on dns_filter.py by Oliver Hitz <oliver@net-track.ch> and the python
examples providen by UNBOUND/Wijngaards/Wouters.

DNS filtering extension for the unbound DNS resolver.

At start, it reads the following files:

- domain.blacklist  : contains a domain or IP per line to block.
- regex.blacklist   : contains a regex per line to match a domain or IP to block.
- domain.whitelist  : contains a domain or IP per line to pass through.
- regex.whitelist   : contains a regex per line to match a domain or IP to pass through.

Note: IP's will only be checked against responses.

For every query sent to unbound, the extension checks if the name is in the
lists and matches. If it is in the whitelist, processing continues
as usual (i.e. unbound will resolve it). If it is in the blacklist, unbound
stops resolution and returns the IP address configured in intercept_address,
or REFUSED reply if left empty.

Note: The whitelist has precedence over blacklist.

The whitelist and blacklist domain matching is done with every requested domain
and includes it subdomains.

The regex versions will match whatever is defined. It will match sequentially
and stops processing after the first hit.

Install and configure:

- Copy dns-firewall.py to unbound directory. 
- If needed, change "intercept_address" below.
- Change unbound.conf as follows:

  server:
    module-config: "python validator iterator"

  python:
    python-script: "/unbound/directory/dns-firewall.py"

- Create the above lists as desired (filenames can be modified below).
- Restart unbound.

TODO:

- Add logging-verbosity feature
- Cleanup/Tweak/Improve response processing. Lots of room for improvement.

=================================================================================
'''

import re

blacklist = set()
whitelist = set()
rblacklist = set()
rwhitelist = set()

# IP Address to redirect to, leave empty to generate REFUSED
intercept_address = '192.168.1.250'

blacklist_file = '/etc/unbound/domain.blacklist'
whitelist_file = '/etc/unbound/domain.whitelist'
rblacklist_file = '/etc/unbound/regex.blacklist'
rwhitelist_file = '/etc/unbound/regex.whitelist'

# Check answers/responses as well
checkresponse = True

def check_name(name, xlist, bw, type):
    #log_info('DNS-FIREWALL: Checking \"' + name + '\" against domain '+ bw + 'list')
    fullname = name
    while True:
        #log_info('DNS-FIREWALL: Checking name \"' + name + '\"')
        if name in xlist:
            log_info('DNS-FIREWALL: ' + type + ' \"' + fullname + '\" matched against ' + bw + 'list-entry \"' + name + '\"')
            return True
        elif name.find('.') == -1:
            return False
        else:
            name = name[name.find('.') + 1:]
    return False


def check_regex(name, xlist, bw, type):
    #log_info('DNS-FIREWALL: Checking \"' + name + '\" against regex '+ bw + 'list')
    for regex in xlist:
        #log_info('DNS-FIREWALL: Checking regex \"' + regex + '\"')
        if re.match(regex, name, re.I | re.M):
            log_info('DNS-FIREWALL: ' + type + ' \"' + name + '\" matched against ' + bw + '-regex \"' + regex + '\"')
            return True
    return False


def read_list(name, xlist):
    log_info('DNS-FIREWALL: Reading file/list \"' + name + '\"')
    try:
        with open(name, 'r') as f:
            for line in f:
                if not line.startswith("#") and not len(line.strip()) == 0:
                    xlist.add(line.rstrip())
        return True
    except IOError:
        log_info('DNS-FIREWALL: Unable to open file ' + name)
    return False


def init(id, cfg):
    log_info('DNS-FIREWALL: Initializing')
    read_list(whitelist_file, whitelist)
    read_list(blacklist_file, blacklist)
    read_list(rwhitelist_file, rwhitelist)
    read_list(rblacklist_file, rblacklist)
    if len(intercept_address) == 0:
        log_info('DNS-FIREWALL: Using REFUSED for matched queries')
    else:
        log_info('DNS-FIREWALL: Using \"' + intercept_address + '\" for matched queries')
    return True


def deinit(id):
    return True


def inform_super(
    id,
    qstate,
    superqstate,
    qdata,
    ):
    return True


def operate(
    id,
    event,
    qstate,
    qdata,
    ):

    if event == MODULE_EVENT_NEW or event == MODULE_EVENT_PASS:

        name = qstate.qinfo.qname_str.rstrip('.')

        #log_info('DNS-FIREWALL: Checking \"' + name + '\" against whitelists')
        if check_name(name, whitelist, 'white', 'QUERY') or check_regex(name, rwhitelist, 'white', 'QUERY'):
            log_info('DNS-FIREWALL: \"' + name + '\" is whitelisted, PASSTHRU')
            qstate.ext_state[id] = MODULE_WAIT_MODULE
            return True

        #log_info('DNS-FIREWALL: Checking \"' + name + '\" against blacklists')
        if check_name(name, blacklist, 'black', 'QUERY') or check_regex(name, rblacklist, 'black', 'QUERY'):
            msg = DNSMessage(qstate.qinfo.qname_str, RR_TYPE_A, RR_CLASS_IN, PKT_QR | PKT_RA | PKT_AA)

            if len(intercept_address) == 0:
                log_info('DNS-FIREWALL: Blocked \"' + name + '\", generated REFUSED')
                invalidateQueryInCache(qstate, qstate.return_msg.qinfo)
                qstate.return_rcode = RCODE_REFUSED
            else:
                log_info('DNS-FIREWALL: Blocked, \"' + name + '\", REDIRECTED to ' + intercept_address)
                if qstate.qinfo.qtype == RR_TYPE_A or qstate.qinfo.qtype == RR_TYPE_ANY:
                    msg.answer.append('%s 10 IN A %s' % (qstate.qinfo.qname_str, intercept_address))
                qstate.return_rcode = RCODE_NOERROR

            if not msg.set_return_msg(qstate):
                qstate.ext_state[id] = MODULE_ERROR
                return False

            qstate.return_msg.rep.security = 2

            qstate.ext_state[id] = MODULE_FINISHED
            return True
        else:
            qstate.ext_state[id] = MODULE_WAIT_MODULE
            return True

    if event == MODULE_EVENT_MODDONE:

        if checkresponse:
            msg = qstate.return_msg
            if msg:
                qname = msg.qinfo.qname_str.rstrip(".")
                if not check_name(qname, whitelist, 'white', 'RESPONSE') and not check_regex(qname, rwhitelist, 'white', 'RESPONSE'):
                    rep = msg.rep
                    for i in range(0,rep.an_numrrsets):
                        rk = rep.rrsets[i].rk
                        data = rep.rrsets[i].entry.data
                        if ntohs(rk.type) in (1, 5, 28):
                            for j in range(0,data.count):
                                answer = data.rr_data[j]
                                length = answer[:2]
                                rawdata = answer[2:]

                                if ntohs(rk.type) == 1:
                                    types = "A"
                                    name = "%d.%d.%d.%d"%(ord(answer[2]),ord(answer[3]),ord(answer[4]),ord(answer[5]))
                                elif ntohs(rk.type) == 5:
                                    types = "CNAME"
                                    wire = ''
                                    for ch in str(rawdata):
					if ( ch >= '0' and ch <= '9' ) or ( ch >= 'a' and ch <= 'z') or ( ch >= 'A' and ch <= 'Z' ) or ( ch == '-' ):
                                            wire += '%c' % ch
                                        else:
                                            wire += '.'
                                    name=wire.strip('.')
                                elif ntohs(rk.type) == 28:
                                    types = "AAAA"
                                    name = "%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x:%02x%02x"%(ord(answer[2]),ord(answer[3]),ord(answer[4]),ord(answer[5]),ord(answer[6]),ord(answer[7]),ord(answer[8]),ord(answer[9]),ord(answer[10]),ord(answer[11]),ord(answer[12]),ord(answer[13]),ord(answer[14]),ord(answer[15]),ord(answer[16]),ord(answer[17]))
                                else:
                                    types = False

                                if types:
                                    #log_info('DNS-FIREWALL: checking RESPONSE \"' + name + '\" (' + types + ')')
                                    if check_name(name, blacklist, 'black', 'RESPONSE') or check_regex(name, rblacklist, 'black', 'RESPONSE'):
                                        log_info('DNS-FIREWALL: Blocked RESPONSE ' + qname + ' -> ' + name + ' (' + types + '), generated REFUSED')
                                        invalidateQueryInCache(qstate, qstate.return_msg.qinfo)
                                        qstate.return_rcode = RCODE_REFUSED
                                        qstate.return_msg.rep.security = 2
                                        qstate.ext_state[id] = MODULE_FINISHED
                                        return True

        qstate.ext_state[id] = MODULE_FINISHED
        return True

    log_err('pythonmod: bad event')
    qstate.ext_state[id] = MODULE_ERROR
    return False

