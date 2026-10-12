from gamefs import gc, which
g = gc()
for pre in ["models/survivors/", "models/infected/", "models/v_models/", "models/weapons/"]:
    fs = sorted(f for f in g.files if f.startswith(pre) and f.endswith(".mdl"))
    print("==", pre, len(fs))
    for f in fs:
        v = [p.split("\\")[-2] for p in which(f)]
        print("  ", f, v)
