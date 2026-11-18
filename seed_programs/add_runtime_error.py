import sys

a, b = map(int, sys.stdin.read().split())
print(a / 0)  # ZeroDivisionError
