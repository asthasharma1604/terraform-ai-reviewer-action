from github import Github, Auth
from google import genai
from google.genai import types
from models import ReviewResponse

SYSTEM_PROMPT = """
You are a Cloud Security & FinOps Expert. Analyze the provided Terraform code.
The code provided to you includes line numbers at the start of each line (e.g., "1: resource...").
Identify security risks, cost optimization opportunities, and provide specific code fixes.
For every issue or fix you find, you MUST specify the exact `file_name` and the exact `line_numbers` (e.g., "15" or "15-20") where the issue occurs based on the provided context.
Provide a clear summary, security risks, cost optimization tips, and a list of specific code fix suggestions.
"""

def fetch_tf_code(repo_name, pr_number, token):
    # Downloads modified Terraform files from the PR and injects line numbers.
    auth = Auth.Token(token)
    gh_client = Github(auth=auth)

    repo = gh_client.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    tf_code_context = ""
    for file in pr.get_files():
        if file.filename.endswith(".tf") and file.status != "removed":
            try:
                content_file = repo.get_contents(file.filename, ref=pr.head.sha)
                raw_content = content_file.decoded_content.decode("utf-8")
                
                # Inject line numbers
                lines = raw_content.split('\n')
                numbered_code = "\n".join([f"{i+1}: {line}" for i, line in enumerate(lines)])
                
                tf_code_context += f"### File: {file.filename}\n```hcl\n{numbered_code}\n```\n\n"
            except Exception as e:
                print(f"Error reading file {file.filename}: {e}")

    return tf_code_context

def analyze_with_gemini(tf_code_context, gemini_key):
    # Sends the formatted code to Gemini and returns structured JSON.
    gemini_client = genai.Client(api_key=gemini_key)
    full_prompt = f"Review this Terraform code:\n\n{tf_code_context}"
    
    response = gemini_client.models.generate_content(
        model='gemini-3.6-flash',
        contents=full_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ReviewResponse,
            temperature=0.2
        )
    )
    
    return response.parsed