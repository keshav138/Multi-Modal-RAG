// Jenkins declarative pipeline for the Multi Modal RAG service.
//
// Runs on your laptop's built-in node (Windows).  Triggers: SCM polling —
// GitHub webhooks can't reach a laptop behind NAT, so Jenkins checks the
// repo every minute instead (adjust pollSCM below).
//
// Flow: checkout -> lint -> test -> docker build -> push to Docker Hub
//       -> (main only) SSH deploy to the EC2 box -> health check.
//
// One-time Jenkins setup (Dashboard -> Manage Jenkins):
//   1. Credentials (Manage Credentials -> System -> Global -> Add):
//        dockerhub    : username/password — Docker Hub access token
//        ec2-ssh-key  : SSH private key    — other half of Terraform's aws_key_pair
//        ec2-host     : secret text        — `terraform output public_ip`
//        groq-api-key : secret text        — Groq API key, written to the box's .env
//   2. Global env var (System -> Global properties -> Environment variables):
//        DOCKERHUB_USER : your Docker Hub username
//   3. Laptop prerequisites: Java 17, Git for Windows (Git Bash), Docker
//      Desktop, Python 3.11 with this repo's requirements installed.
//   4. The Docker Hub repo 'multimodal-rag' must exist and be PUBLIC (or
//      add a docker login step for the box inside the Deploy stage).
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
        // Declarative pipelines auto-checkout when the job has SCM defined
        // (see the "Declarative: Checkout SCM" step), so there's no explicit
        // checkout here — just derive the build tag from the commit SHA.
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
                bat 'docker build -f infra/docker/Dockerfile -t %FULL_IMAGE%:%BUILD_TAG% .'
            }
        }

        stage('Push') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub',
                    usernameVariable: 'DH_USER',
                    passwordVariable: 'DH_TOKEN'
                )]) {
                    // `<nul set /p=` echoes WITHOUT a trailing CRLF — plain
                    // `echo x |` would append \r\n and Docker sends the token
                    // with a stray \r, failing auth on Windows.
                    bat '<nul set /p="%DH_TOKEN%" | docker login -u %DH_USER% --password-stdin'
                    bat 'docker tag %FULL_IMAGE%:%BUILD_TAG% %FULL_IMAGE%:latest'
                    bat 'docker push %FULL_IMAGE%:%BUILD_TAG%'
                    bat 'docker push %FULL_IMAGE%:latest'
                }
            }
        }

        stage('Deploy') {
            when { branch 'main' }
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
                    // Build the box's .env locally (avoids heredoc-over-SSH
                    // quoting pain), scp it with the compose file, then pull
                    // the fresh image and restart the stack.
                    sh '''
                        set -e
                        printf 'DATABASE_URL=postgresql://rag:rag@db:5432/rag\\nGROQ_API_KEY=%s\\nDOCKERHUB_USER=%s\\nIMAGE_TAG=%s\\n' \\
                          "$GROQ_KEY" "$DOCKERHUB_USER" "$BUILD_TAG" > .env.deploy

                        chmod 600 "$SSH_KEY"
                        scp -o StrictHostKeyChecking=no -i "$SSH_KEY" \
                          .env.deploy "$SSH_USER@$EC2_HOST:$DEPLOY_DIR/.env"
                        scp -o StrictHostKeyChecking=no -i "$SSH_KEY" \
                          infra/docker/docker-compose.yml "$SSH_USER@$EC2_HOST:$DEPLOY_DIR/"
                        ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_HOST" \
                          "cd $DEPLOY_DIR && docker compose pull && docker compose up -d"
                        rm -f .env.deploy
                    '''
                }
            }
        }

        stage('Verify') {
            when { branch 'main' }
            steps {
                withCredentials([
                    sshUserPrivateKey(
                        credentialsId: 'ec2-ssh-key',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    ),
                    string(credentialsId: 'ec2-host', variable: 'EC2_HOST')
                ]) {
                    sh '''
                        ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$EC2_HOST" \
                          "curl -fsS http://localhost:8000/health && curl -fsS http://localhost:8000/readiness"
                    '''
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
