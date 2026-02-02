#!/usr/bin/python3

import subprocess
import logging
import sys, datetime
from systemd.journal import JournalHandler

def serviceActive(iPort, sName, iMaxIdle):
    try:
        sOut = subprocess.check_output('/bin/netstat --tcp --numeric | grep --count ":%d"' % (iPort), shell=True)
        iOut = int(sOut.decode())
        bActive = (iOut > iMaxIdle)
        log.info("%s: Habe gefunden:%d, maxIdle:%d, isActive:%d" % (sName, iOut, iMaxIdle, bActive))
        if bActive:
            return True
    except subprocess.CalledProcessError as e:
        log.info('%s: inactive (%s)' % (sName, e))
    return False

def sambaActive():
    try:
        # find all smb connections, except the servers connections (to itself)
        sOut = subprocess.check_output('/bin/netstat --tcp --numeric | grep ":445" | cut -b45-68 | grep -v "192.168.178.150"', shell=True)
        log.info("samba: Habe gefunden:%s" % (sOut))
        return True
    except subprocess.CalledProcessError as e:
        log.info('samba: inactive (%s)' % e)
    return False

def psActive(sName, iMaxIdle):
    try:
        sOut = subprocess.check_output('/usr/bin/pgrep %s' % (sName), shell=True)
        iCnt = nlcount(sOut)
        bActive = (iCnt > iMaxIdle)
        log.info("%s: habe gefunden:%d, maxIdle:%d, isActive:%d" % (sName, iCnt, iMaxIdle, bActive))
        if bActive:
            return True
    except subprocess.CalledProcessError as e:
        log.info('%s: incative (%s)' % (sName, e))

    return False

def vdrRecording():
    try:
        sOut = subprocess.check_output('ls /tmp/vdrec_*', shell=True, stderr=subprocess.STDOUT)
        log.info('vdrRec: found [%s]' % sOut)
        return True
    except subprocess.CalledProcessError as e:
        log.info('inactive VDR recording (%s)' % e)

    return False

def vdrGetTimerList_():
    dates = list()
    with open('/var/lib/vdr/timers.conf') as f:
        for line in f:
            # Example line: 9:S19.2E-1-1082-20002:2018-02-26:2004:2053:99:99:The...
            parts = line.split(':')
            dt = int(datetime.datetime.strptime(parts[2] + '_' + parts[3], '%Y-%m-%d_%H%M').timestamp())
            dates.append(dt)
    dates.sort()
    return dates

def vdrTimerVeryClose():
    iClose = 1200  # 20min
    now = datetime.datetime.now().timestamp()

    dates = vdrGetTimerList_()
    log.info("Now is %u" % now)
    for d in dates:
        delta = d - now
        if now >= d:
            log.info('Timer  %u (%d) in the past, assume active recording, ignore.' % (d, delta))
        if now < d:
            if (now + iClose) < d:
                log.info('Timer  %u (%d) in the far future' % (d, delta))
                return False
            else:
                log.info('Timer  %u (%d) close to now' % (d, delta))
                return True
    return False  # no timer

def vdrGetNextTimer():
    now = datetime.datetime.now().timestamp()
    dates = vdrGetTimerList_()
    log.info("Now+5min is %u" % now)
    for d in dates:
        d = d - 300  # 5min earlier due reboot time
        delta = d - now
        if now >= d:
            log.info('Timer  %u (%d) in the past, assume active recording, ignore.' % (d, delta))
        if now < d:
            log.info('Timer  %u (%d) useable for RTC wakeup.' % (d, delta))
            return d

    return 0  # no timer

def minUptime(iMinSeconds):
    with open('/proc/uptime', 'r') as f:
        iSecondsUp = int(float(f.readline().split()[0]))
    bActive = (iMinSeconds > iSecondsUp)
    log.info("uptime: bActive:%u, iMinSeconds:%u, UpSeconds:%u" % (bActive, iMinSeconds, iSecondsUp))
    return bActive

# Helper
def nlcount(buf):
    nl_count = 0  # Number of new line characters
    tot_count = 0  # Total number of characters
    for character in buf.decode():
        if character == '\n':
            nl_count += 1
        tot_count += 1
    return nl_count

def printHelp():
    print("usage %s --shutdowncheck|--nexttimer" % sys.argv[0])

def is_container_running(container_name):
    try:
        # Führt den Befehl 'pct list' aus, der alle LXC-Container anzeigt
        result = subprocess.run(['pct', 'list'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Überprüfen, ob der Containername in der Ausgabe vorhanden ist und ob der Status "running" ist
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                # Beispielhafte Ausgabe: 100 | prxmxvdr | running | 192.168.1.100
                if container_name in line and "running" in line:
                    log.info('prxmxvdr is on')
                    return True
        log.info('prxmxvdr is off')
        return False  # no container
    except Exception as e:
        print(f"Fehler beim Prüfen des Container-Status: {e}")
        return False

# Main program
log = logging.getLogger('autoshutdown')
log.addHandler(JournalHandler())
log.setLevel(logging.INFO)

if len(sys.argv) != 2:
    printHelp()
elif sys.argv[1] == '--shutdowncheck':
    bActive = False
    bActive |= serviceActive(22, "ssh", 0)
    bActive |= serviceActive(34890, "vnsiserver", 1)
    bActive |= serviceActive(3000, "streamdevserver", 0)
    bActive |= sambaActive()
    # bActive |= psActive("firefox", 0)
    # bActive |= vdrRecording()
    bActive |= minUptime(30 * 60)
    # bActive |= vdrTimerVeryClose()
    bActive |= is_container_running("prxmxvdr")

    if bActive:
        log.info("Ready shutdown: no")
        sys.exit(1)
    else:
        log.info("Ready shutdown: yes")
        #sys.exit(0)
        subprocess.run("/sbin/shutdown -h now", shell=True)
        #print("/sbin/shutdown -h now\n")
elif sys.argv[1] == '--nexttimer':
    print("%u" % vdrGetNextTimer(), end='', flush=True)
    sys.exit(0)
else:
    printHelp()
    sys.exit(1)
