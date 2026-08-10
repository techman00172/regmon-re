 #!/bin/sh 
 read -r -p "CAUTION *** Are you REALLY SURE you want to DELETE this projects DIR and FOSSIL REPO  (y)"  response 
 case $response in 
    [yY][eE][sS]|[yY]) 
    #if yes, then execute the passed parameters 
	       echo 'check for fslckout' 
		  if [ -e library/.fslckout ]; then 
		     echo 'Fossil Forth Library checkout found, closing it... !' 
		     cd library || exit 1
		     fossil close -f 
		     echo 'Fossil Forth Library checkout closed' 
		     cd ../ || exit 1
		  else 
		     echo 'No Fossil Forth Library Checkout found' 
		  fi 
            echo 'Deleting this directory and its Fossil Repo' 
		  fossil close -f 
		  rm /home/tp/fossil/regmon-re.fossil* 
		  rm -rf /home/tp/fossil/regmon-re/.fossil-settings 
		  rm /home/tp/fossil/regmon-re/.vimrc 
		  rm -rf /home/tp/fossil/regmon-re/* 
            ;; 
    *) 
              #Otherwise exit... 
              echo " Not deleting anything!" 
              exit 
              ;; 
 esac 
