const core = require('@actions/core');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DEFAULT_MODES = [
  // Run the complete Terraform review workflow.
  'analyze',
  'security',
  'cost',
  'architecture',
  'dangerous',
  'fixes',
  'inline'
];

function run() {
  try {
    // Read values configured by the consuming workflow.
    const githubToken = core.getInput('github_token', { required: true });
    const openaiKey = core.getInput('openai_api_key', { required: true });
    const planPath = core.getInput('plan_path') || '';
    const event = readEventPayload();

    const env = {
      ...process.env,
      GITHUB_TOKEN: githubToken,
      OPENAI_API_KEY: openaiKey,
      PLAN_PATH: planPath,
      PR_NUMBER: process.env.PR_NUMBER || event.pull_request?.number?.toString() || '',
      REPO_NAME: process.env.REPO_NAME || process.env.GITHUB_REPOSITORY || '',
      GITHUB_SHA: process.env.GITHUB_SHA || ''
    };

    // Resolve files from this action, not the consumer repository.
    const actionRoot = process.env.GITHUB_ACTION_PATH || path.resolve(__dirname, '..');
    const reviewScript = path.join(actionRoot, 'review.py');
    const requirementsFile = path.join(actionRoot, 'requirements.txt');

    // Keep Python dependencies isolated in the runner's temporary directory.
    const venvRoot = path.join(process.env.RUNNER_TEMP || '/tmp', 'terraform-ai-reviewer-venv');
    const pythonPath = process.platform === 'win32'
      ? path.join(venvRoot, 'Scripts', 'python.exe')
      : path.join(venvRoot, 'bin', 'python');

    // Install the Python packages required by review.py.
    execFileSync('python3', ['-m', 'venv', venvRoot], {
      env,
      stdio: 'ignore'
    });
    execFileSync(pythonPath, ['-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', requirementsFile], {
      env,
      stdio: 'inherit'
    });

    // Execute each review mode with the prepared Python interpreter.
    for (const mode of DEFAULT_MODES) {
      execFileSync(pythonPath, [reviewScript, mode], {
        env,
        stdio: 'inherit'
      });
    }
  } catch (error) {
    // Convert any setup or review failure into an Action failure.
    core.setFailed(error.message);
  }
}

// Read the event payload to support pull request runs without custom variables.
function readEventPayload() {
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!eventPath) {
    return {};
  }

  try {
    return JSON.parse(fs.readFileSync(eventPath, 'utf8'));
  } catch {
    return {};
  }
}

run();