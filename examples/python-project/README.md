# Python Project Example

A simple Python project demonstrating Cognition usage.

## Setup

```bash
# Navigate to this project
cd examples/python-project

# Initialize Cognition for this project
cognition init --project

# Install Python dependencies
pip install -r requirements.txt
```

## Using Cognition

```bash
# Start server in one terminal
cognition serve

# In another terminal, start TUI
cognition-client
```

## Example Interactions

### Ask about the project

```
What does this project do?
```

### Request a new feature

```
Add a function to calculate factorial with error handling and tests.
```

### Debug an issue

```
There's a bug in the main.py file. Can you find and fix it?
```

### Run tests

```
Run the tests and tell me if they pass.
```

## Project Structure

```
python-project/
├── README.md              # This file
├── main.py                # Main application
├── utils.py               # Utility functions
├── requirements.txt       # Python dependencies
└── .cognition/
    └── config.yaml        # Project-specific config
```
