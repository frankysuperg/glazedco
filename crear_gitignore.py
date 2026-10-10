lineas = [
    ".env*",
    "*.env",
    ".venv/",
    ".vs/",
    "__pycache__/",
    "data/",
]

with open(".gitignore", "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lineas) + "\n")

print("Listo. .gitignore reescrito:")
print(open(".gitignore", encoding="utf-8").read())