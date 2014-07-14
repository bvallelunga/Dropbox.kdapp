#!/bin/bash
DROPBOX="/home/$1/Dropbox"
HELPER="python /home/$1/.dropbox-app/dropbox.py"
EXCLUDE_FILES=""

exclude() {
  mkdir -p $DROPBOX;
  mkdir -p $DROPBOX/Koding;
  
  if [[ $($HELPER status) != "Dropbox isn't running!" ]]; then
    for file in $DROPBOX/*; do
      if [[ $file != $DROPBOX/Koding ]]; then
        
        # Dropbox python script cant read paths with spaces in
        # it becuase of how it parses arguments from cli.
        # This replaces spaces with ||. Then the python
        # script goes back and converts it back to a space.
        EXCLUDE_FILES+="${file// /||} ";
      fi
    done
  
    if [[ $EXCLUDE_FILES != "" ]]; then
      $HELPER exclude add $EXCLUDE_FILES;
    fi
  fi
}

if [[ $2 == "true" ]]; then
  while true; do
    exclude;
    sleep 5m;
  done
else
  exclude;
fi