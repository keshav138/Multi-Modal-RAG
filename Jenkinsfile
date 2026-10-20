// Jenkins declarative pipeline for the Multi Modal RAG service.
//
// Runs on your laptop's built-in node (Windows).  Trigger: SCM polling —
// GitHub webhooks can't reach a laptop behind NAT, so Jenkins checks the
// repo every minute instead.
//
// The Jenkinsfile is intentionally thin: every stage that touches Windows /
// Docker / SSH specifics lives in scripts/ci/*.cmd, so those scripts can be
// run and debugged directly in a terminal, outside Jenkins.
//
// Flow: lint -> test -> build -> push to Docker Hub -> (main) SSH deploy -> health check.
//
// One-time Jenkins setup (Dashboard -> Manage Jenkins):
//   1. Credentials (Manage Credentials -> System -> Global -> Add):
//        dockerhub    : username/password — Docker Hub access token
//        ec2-ssh-key  : SSH private key    — other half of Terraform's aws_key_pair
//        ec2-host     : secret text        — `terraform output public_ip`
//        groq-api-key : secret text        — Groq API key, written to the box's .env
//   2. Global env var (System -> Global properties -> Environment variables):
//        DOCKERHUB_USER : your Docker Hub username
//   3. Laptop prerequisites: Java 17, Git for Windows, Docker Desktop,
//      Python 3.11 with this repo's requirements installed.
//   4. The Docker Hub repo 'multimodal-rag' must exist and be PUBLIC.
//   5. Job: New Item -> Pipeline -> Definition "Pipeline script from SCM"
//      -> Git -> repo URL -> Script Path: Jenkinsfile.

pipeline {
    agent any

    environment {
        IMAGE_NAME = 'multimodal-rag'
        FULL_IMAGE = "docker.io/${env.DOCKERHUB_USER}/${IMAGE_NAME}"
        DEPLOY_DIR = '/opt/mmrag'
    }

    triggers {
        pollSCM('* * * * *')   // check GitHub every minute
    }

    stages {
        // Declarative pipelines auto-checkout when the job has SCM defined,
        // so there's no explicit checkout here — just derive the build tag.
        stage('Set build tag') {
            steps {
                script {
                    env.BUILD_TAG = env.GIT_COMMIT ?: 'latest'
                }
            }
        }

        stage('Lint') {
            steps {
                bat 'ruff check app tests pipelines scripts'
            }
        }

        stage('Test') {
            steps {
                bat 'pytest tests/ -v --cov=app --cov-report=term-missing'
            }
        }

        stage('Build') {
            steps {
                bat 'scripts\\ci\\build-image.cmd'
            }
        }

        stage('Push') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub',
                    usernameVariable: 'DH_USER',
                    passwordVariable: 'DH_TOKEN'
                )]) {
                    bat 'scripts\\ci\\push-image.cmd'
                }
            }
        }

        stage('Deploy') {
            when {
                // `when { branch 'main' }` is unreliable in simple pipeline
                // jobs (no BRANCH_NAME), so check the env vars directly.
                expression {
                    def branch = env.GIT_BRANCH ?: env.BRANCH_NAME ?: 'main'
                    return branch == 'main' || branch.endsWith('/main')
                }
            }
            steps {
                withCredentials([
                    sshUserPrivateKey(
                        credentialsId: 'ec2-ssh-key',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    ),
                    string(credentialsId: 'ec2-host', variable: 'EC2_HOST'),
                    string(credentialsId: 'groq-api-key', variable: 'GROQ_KEY')
                ]) {
                    bat 'scripts\\ci\\deploy.cmd'
                }
            }
        }

        stage('Verify') {
            when {
                expression {
                    def branch = env.GIT_BRANCH ?: env.BRANCH_NAME ?: 'main'
                    return branch == 'main' || branch.endsWith('/main')
                }
            }
            steps {
                withCredentials([
                    sshUserPrivateKey(
                        credentialsId: 'ec2-ssh-key',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    ),
                    string(credentialsId: 'ec2-host', variable: 'EC2_HOST')
                ]) {
                    bat 'scripts\\ci\\verify.cmd'
                }
            }
        }
    }

    post {
        always {
            catchError(buildResult: 'SUCCESS', stageResult: 'SUCCESS') {
                bat 'docker image prune -f'
            }
        }
    }
}
