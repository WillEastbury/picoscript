import subprocess, struct
out = subprocess.check_output(["python3","picoscript_build.py","emit","host/picowal/test_route.eng","--as","bytecode","--hex"], cwd="/mnt/c/source/Picoscript").decode()
words = [int(x,16) for x in out.split()]
with open("/tmp/test_route_raw.bin","wb") as f:
    for w in words:
        f.write(struct.pack("<I", w))
print("wrote", len(words), "words,", len(words)*4, "bytes")