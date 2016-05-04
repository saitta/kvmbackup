### Kvmbackup
Backup of kvm virtual guests.

### status
offline backup is used in small production environment.
live backup does not work with current libvirt 
(blockcopy always failed when with option "--pivot")

https://bugzilla.redhat.com/show_bug.cgi?id=1197592

### Usage                        
usage: kvm_backup.py [-h] [-d DEST] [-k KEEP] [-r RATE] [-t TIMEOUT] [-n]
  [--remove_tmp_file] [-D DISKS] [--noactive]
  [--force_noactive]
  vms [vms ...]

positional arguments:
  vms                   virtual machines to backup

optional arguments:
  -h, --help            show this help message and exit
  
  -d DEST, --dest DEST  Backup destination folder
  
  -k KEEP, --keep KEEP  Number of backups to keep
  
  -r RATE, --rate RATE  bandwith limit in MiB/s ex. 20
  
  -t TIMEOUT, --timeout TIMEOUT
                        Number of minutes to wait for blockcommit to finish
                        
  -n, --dryrun          do not perform backup just inform
  
  --remove_tmp_file     remove external snapshot file(s) when blockcommit is
                        finished
                        
  -D DISKS, --disks DISKS
                        backup all disks if this list of disks is empty. This
                        option can be used multiple times
                        
  --noactive            do not perform perform backup if host is on
  
  --force_noactive      shutdown vm and do offline backup
                        
