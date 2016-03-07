"""x = raw_input("input x")
y = raw_input("input y")

z = int(x) + int(y)

#print z
"""

string = raw_input("type ")
z = len(string)

if z < 2:
    print ""
else:
    print string[:2] + string[-2:]
