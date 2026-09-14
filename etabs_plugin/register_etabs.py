import os

DLL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "EtabsLiveConnector.dll")
PLUGIN_NAME = "Live Connector"

ini_path = os.path.expandvars(r"%LOCALAPPDATA%\Computers and Structures\ETABS 23\ETABS.ini")

if not os.path.exists(ini_path):
    print("ETABS.ini not found")
    raise SystemExit(0)

with open(ini_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the [PlugIn] section. ETABS rewrites this file itself (it regenerated
# ours with different indentation/comments overnight), so this edits only
# the plugin entries in place rather than round-tripping the whole file
# through configparser, which risks reformatting sections ETABS validates
# strictly on its own.
section_start = None
section_end = len(lines)
for i, line in enumerate(lines):
    if line.strip() == "[PlugIn]":
        section_start = i
        for j in range(i + 1, len(lines)):
            if lines[j].lstrip().startswith("[") and lines[j].strip() != "[PlugIn]":
                section_end = j
                break
        else:
            section_end = len(lines)
        break

if section_start is None:
    # No [PlugIn] section yet -- append one at the end of the file.
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append("[PlugIn]\n")
    section_start = len(lines) - 1
    section_end = len(lines)

section = lines[section_start + 1:section_end]

# .NET plugins are identified to ETABS by DLL path, not a COM ProgID --
# CSI's own "Add/Show Plugins" dialog populates AssemblyOrProgID this way
# for a ".NET plugin" (only "COM plugin" entries use a ProgID), and that
# is what actually survives ETABS's own startup validation.
already_registered = any(DLL_PATH.lower() in line.lower() for line in section)

if already_registered:
    print("Already exists in ETABS.ini")
else:
    num_plugins = 0
    for line in section:
        if "=" in line and line.strip().lower().startswith("numberplugins"):
            try:
                num_plugins = int(line.split("=", 1)[1].strip())
            except ValueError:
                num_plugins = 0
            break

    new_index = num_plugins + 1
    new_entries = [
        f"Name{new_index}={PLUGIN_NAME}\n",
        f"AssemblyOrProgID{new_index}={DLL_PATH}\n",
        f"IsDotNet{new_index}=True\n",
    ]

    updated_section = []
    found_count_line = False
    for line in section:
        if "=" in line and line.strip().lower().startswith("numberplugins"):
            updated_section.append(f"NumberPlugIns={new_index}\n")
            found_count_line = True
        else:
            updated_section.append(line)
    if not found_count_line:
        updated_section.insert(0, f"NumberPlugIns={new_index}\n")
    updated_section.extend(new_entries)

    lines[section_start + 1:section_end] = updated_section

    with open(ini_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("Successfully added to ETABS.ini")
