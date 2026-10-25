import os
import zipfile

def create_zip(zip_name: str = "refractory_lining_copilot.zip"):
    exclude_dirs = {
        ".git", "venv", ".venv", "env", ".pytest_cache",
        "__pycache__", "scratch", "logs"
    }
    exclude_files = {
        "Lining_Copilot_Source.zip", "server_update.zip", "refractory_lining_copilot.zip",
        "patch.py", "patch_fix.py", "patch_fix2.py", "failed_payload.json"
    }

    included = []
    print(f"Creating {zip_name}...")
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for root, dirs, files in os.walk("."):
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.endswith(".egg-info")]
            rel_root = os.path.relpath(root, ".").replace("\\", "/")
            if any(part in exclude_dirs for part in rel_root.split("/")):
                continue

            for f in sorted(files):
                if f in exclude_files or f.endswith(".pyc") or f.endswith(".zip"):
                    continue

                rel_path = os.path.join(rel_root, f) if rel_root != "." else f
                rel_path = rel_path.replace("\\", "/")

                # Exclude runtime generated outputs, but preserve folder structure (.gitkeep)
                if rel_path.startswith("data/outputs/") and f != ".gitkeep":
                    continue

                zipf.write(rel_path, rel_path)
                included.append(rel_path)

    size_mb = os.path.getsize(zip_name) / (1024 * 1024)
    print(f"Successfully packaged {len(included)} files into '{zip_name}' ({size_mb:.2f} MB).")
    return zip_name

if __name__ == "__main__":
    create_zip()
