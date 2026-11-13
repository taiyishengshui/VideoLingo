from ruamel.yaml import YAML
import os
import threading

# config.yaml lives in the PROJECT ROOT, not the cwd. Batch jobs run with
# cwd = a per-video workspace (see core/utils/job_context.py), so resolving
# the config relative to this file's location keeps settings shared across
# all workspaces. VL_CONFIG override allows tests / alternate installs.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.environ.get("VL_CONFIG") or os.path.join(_PROJECT_ROOT, 'config.yaml')
lock = threading.Lock()

yaml = YAML()
yaml.preserve_quotes = True

# -----------------------
# load & update config
# -----------------------

def load_key(key):
    with lock:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
            data = yaml.load(file)

    keys = key.split('.')
    value = data
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            raise KeyError(f"Key '{k}' not found in configuration")
    return value

def update_key(key, new_value):
    with lock:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
            data = yaml.load(file)

        keys = key.split('.')
        current = data
        for k in keys[:-1]:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return False

        if isinstance(current, dict) and keys[-1] in current:
            current[keys[-1]] = new_value
            with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
                yaml.dump(data, file)
            return True
        else:
            raise KeyError(f"Key '{keys[-1]}' not found in configuration")
        
# basic utils
def get_joiner(language):
    if language in load_key('language_split_with_space'):
        return " "
    elif language in load_key('language_split_without_space'):
        return ""
    else:
        raise ValueError(f"Unsupported language code: {language}")

if __name__ == "__main__":
    print(load_key('language_split_with_space'))
