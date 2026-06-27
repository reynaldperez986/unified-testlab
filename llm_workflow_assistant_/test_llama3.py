import subprocess

prompt = "Test connection: say 'LLAMA3 OK'."

result = subprocess.run(
    ["ollama", "run", "llama3"],
    input=prompt.encode("utf-8"),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

print(result.stdout.decode())
print("Errors:", result.stderr.decode())