import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import os

def modify_files(function_name, file_list, stage):
    script_directory = os.path.dirname(__file__)
    script_directory = os.path.dirname(script_directory)
    for file in file_list:
        file_path = os.path.join(script_directory, file)
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            # Replace all occurrences of "dummy_repo" with the function name
            modified_content = content.replace('dummy_repo', function_name)
            # Replace all occurrences of "dev" or "prd" with the specified stage
            modified_content = modified_content.replace('dev', stage).replace('prd', stage)
            # Write the modified content back to the file
            with open(file_path, 'w') as f:
                f.write(modified_content)
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")

def rename_python_file(function_name):
    script_directory = os.path.dirname(__file__)
    script_directory = os.path.dirname(script_directory)
    os.rename(os.path.join(script_directory, "dummy_repo.py"), os.path.join(script_directory, function_name + ".py"))

def start_process():
    try:
        function_name = function_entry.get()
        stage = stage_entry.get()
        file_list = ['Dockerfile', 'dummy_repo.py', 'task-definition.json', 'setup.py']  # Add or modify file names as needed
        modify_files(function_name, file_list, stage)
        rename_python_file(function_name=function_name)
        # Update GUI with completion message

        # Display success dialog
        messagebox.showinfo("Success", "Files modified successfully!")
    except:
        pass
    # Close the program
    root.destroy()

# GUI setup
root = tk.Tk()
root.title("GitHub Repo Modifier")

# Create a frame for better organization
main_frame = ttk.Frame(root, padding="10")
main_frame.grid(column=0, row=0, sticky=(tk.W, tk.E, tk.N, tk.S))

tk.Label(main_frame, text="Function Name:").grid(row=0, column=0, pady=5, sticky=tk.W)
function_entry = ttk.Entry(main_frame)
function_entry.grid(row=0, column=1, pady=5, padx=5, sticky=tk.W+tk.E)

tk.Label(main_frame, text="Stage (prd or dev):").grid(row=1, column=0, pady=5, sticky=tk.W)
stage_entry = ttk.Entry(main_frame)
stage_entry.grid(row=1, column=1, pady=5, padx=5, sticky=tk.W+tk.E)

start_button = ttk.Button(main_frame, text="Start", command=start_process)
start_button.grid(row=2, column=0, columnspan=2, pady=10)

# Set resizing behavior
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)
main_frame.columnconfigure((0, 1), weight=1)

root.mainloop()