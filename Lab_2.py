import sys
sysargv = ["", "/tmp/task_list.txt"]
fd=open(sysargv[1])
tasks = fd.readlines()
fw=open(sysargv[1],"w")
##tasks = ["a", "b"]
y = raw_input("insert a new task ")

tasks.append(y)
x = raw_input("insert the task to remove ")

if x in tasks:
    tasks.remove(x)
else:
    print "element doesn't exist"
print tasks

fw.writelines(tasks)

fd.close()
fw.close()