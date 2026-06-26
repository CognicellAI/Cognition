# langchain-aws-lambda-microvms

Deep Agents sandbox backend for AWS Lambda MicroVMs.

The backend launches a Lambda MicroVM from a prebuilt image, creates an
in-memory proxy auth token, and speaks to a command server inside the MicroVM.
It implements the Deep Agents `BaseSandbox` contract with:

- `execute()` via the runtime `/execute` route.
- `upload_files()` via `/upload` for workspace files.
- `download_files()` via `/download` for workspace files.
- `terminate()` via the Lambda MicroVM control plane.

V1 intentionally does not build or update MicroVM images.
