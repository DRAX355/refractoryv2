import os
import zipfile

def create_zip():
    zip_name = 'Lining_Copilot_Source.zip'
    include_dirs = ['app', 'data', 'inputs', 'info', 'ui', 'tests']
    include_files = ['requirements.txt', 'README.md', 'pytest.ini']
    
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for d in include_dirs:
            for root, dirs, files in os.walk(d):
                if '__pycache__' in dirs:
                    dirs.remove('__pycache__')
                if '.pytest_cache' in dirs:
                    dirs.remove('.pytest_cache')
                for f in files:
                    file_path = os.path.join(root, f)
                    zipf.write(file_path, os.path.relpath(file_path, '.'))
        
        for f in include_files:
            if os.path.exists(f):
                zipf.write(f)

    print(f"Successfully created {zip_name}!")

if __name__ == '__main__':
    create_zip()
