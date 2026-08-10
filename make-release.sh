#!/bin/sh
# Relocatable script for project releases. Terry Porter 1 Mar 2021.

PROJECT=$(basename "$PWD")
RELEASE=$(fossil status | awk '/^checkout:/ {print $2}' | cut -c1-4)
DATE=$(date +%d.%m.%y)
EXCLUDES="fossil-server-start.sh,z-WARNING-DESTROY*.fossil.sh,doc/Makefile,doc/readme.rst,library-checkout.sh,library-close.sh,make-release.sh,doc/notes.*"

fossil zip trunk "$PROJECT-$DATE-F$RELEASE.zip" --name "$PROJECT" --exclude "$EXCLUDES"
