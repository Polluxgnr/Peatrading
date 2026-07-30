import os

path = "05_interfaces/terminal_dashboard.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "st.markdown" in line and "###" in line:
        if i > 0 and lines[i-1].strip() == "if True:":
            spaces = len(lines[i-1]) - len(lines[i-1].lstrip())
            # Ensure line[i] has 4 more spaces than line[i-1]
            lines[i] = (" " * (spaces + 4)) + line.lstrip()

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)
