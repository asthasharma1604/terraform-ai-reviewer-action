const core = require('@actions/core');
const { execFileSync } = require('child_process');
const path = require('path');

const DEFAULT_MODES = [
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
    const githubToken = core.getInput('github_token', { required: true });
    const openaiKey = core.getInput('openai_api_key', { required: true });
    const planPath = core.getInput('plan_path') || '';

    const env = {
      ...process.env,
      GITHUB_TOKEN: githubToken,
      OPENAI_API_KEY: openaiKey,
      PLAN_PATH: planPath,
      PR_NUMBER: process.env.PR_NUMBER || '',
      REPO_NAME: process.env.REPO_NAME || '',
      GITHUB_SHA: process.env.GITHUB_SHA || ''
    };

    const actionRoot = process.env.GITHUB_ACTION_PATH || path.resolve(__dirname, '..');
    const reviewScript = path.join(actionRoot, 'review.py');

    for (const mode of DEFAULT_MODES) {
      execFileSync('python3', [reviewScript, mode], {
        env,
        stdio: 'inherit'
      });
    }
  } catch (error) {
    core.setFailed(error.message);
  }
}

run();