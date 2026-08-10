import os
import pandas as pd
from pathlib import Path

#File names
data = 'data'
data_file = 'outputdata.csv'

# Full paths for files
root_dir = Path(__file__).parent.parent
data_dir = os.path.join(root_dir, data)

os.makedirs(data_dir, exist_ok=True) 

raw_file = os.path.join(data_dir, data_file)
