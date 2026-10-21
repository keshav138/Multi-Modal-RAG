// Jenkins declarative pipeline for the Multi Modal RAG service.
//
// Runs on your laptop's built-in node (Windows).  Trigger: SCM polling —
// GitHub webhooks can't reach a laptop behind NAT, so Jenkins checks the
// repo every minute instead.
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
                    string(credentialsId: 'ec2-host',     variable: 'EC2_HOST'),
                    string(credentialsId: 'groq-api-key', variable: 'GROQ_KEY')
                ]) {
                    // PowerShell lets us properly break ACL inheritance on the temp key
                    // file that Jenkins writes with over-broad BUILTIN\Users permissions.
                    // SetAccessRuleProtection($true, $false) disables inheritance AND
                    // discards inherited entries, leaving only the rule we set explicitly.
                    powershell '''
                        # docker/ssh write progress to stderr; don't let PowerShell treat that as a failure
                        $ErrorActionPreference = 'Continue'
                        $key = $env:SSH_KEY.Replace("/", "\\")

                        # 1. Fix ACL: break inheritance and restrict to current user only
                        $acl = Get-Acl $key
                        $acl.SetAccessRuleProtection($true, $false)
                        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                            [Security.Principal.WindowsIdentity]::GetCurrent().Name,
                            "FullControl", "Allow"
                        )
                        $acl.SetAccessRule($rule)
                        Set-Acl -Path $key -AclObject $acl

                        # 2. Normalize key: LF endings, guaranteed trailing newline, no BOM
                        $text = [System.IO.File]::ReadAllText($key) -replace "`r`n", "`n"
                        $text = $text.TrimEnd() + "`n"
                        [System.IO.File]::WriteAllText($key, $text, (New-Object System.Text.UTF8Encoding($false)))

                        # 3. Fail fast with a clear message if the key is still unreadable
                        ssh-keygen -y -f $key | Out-Null
                        if ($LASTEXITCODE -ne 0) { throw "ec2-ssh-key credential is not a valid private key" }

                        $lines  = "DATABASE_URL=postgresql://rag:rag@db:5432/rag",
                                  "GROQ_API_KEY=$($env:GROQ_KEY)",
                                  "DOCKERHUB_USER=$($env:DOCKERHUB_USER)",
                                  "IMAGE_TAG=$($env:BUILD_TAG)"
                        $lines | Set-Content .env.deploy

                        scp -o StrictHostKeyChecking=no -i $key .env.deploy "$($env:SSH_USER)@$($env:EC2_HOST):$($env:DEPLOY_DIR)/.env"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
                        scp -o StrictHostKeyChecking=no -i $key infra/docker/docker-compose.yml "$($env:SSH_USER)@$($env:EC2_HOST):$($env:DEPLOY_DIR)/docker-compose.yml"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
                        scp -r -o StrictHostKeyChecking=no -i $key infra/monitoring "$($env:SSH_USER)@$($env:EC2_HOST):/tmp/"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
                        ssh -o StrictHostKeyChecking=no -i $key "$($env:SSH_USER)@$($env:EC2_HOST)" "sudo mkdir -p $($env:DEPLOY_DIR)/../monitoring && sudo rm -rf $($env:DEPLOY_DIR)/../monitoring/prometheus.yml $($env:DEPLOY_DIR)/../monitoring/grafana && sudo cp -r /tmp/monitoring/. $($env:DEPLOY_DIR)/../monitoring/ && rm -rf /tmp/monitoring"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
                        ssh -o StrictHostKeyChecking=no -i $key "$($env:SSH_USER)@$($env:EC2_HOST)" "cd $($env:DEPLOY_DIR) && docker compose pull && docker compose up -d"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
                        Remove-Item .env.deploy -ErrorAction SilentlyContinue
                    '''
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
                    powershell '''
                        # docker/ssh write progress to stderr; don't let PowerShell treat that as a failure
                        $ErrorActionPreference = 'Continue'
                        $key = $env:SSH_KEY.Replace("/", "\\")

                        # 1. Fix ACL: break inheritance and restrict to current user only
                        $acl = Get-Acl $key
                        $acl.SetAccessRuleProtection($true, $false)
                        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                            [Security.Principal.WindowsIdentity]::GetCurrent().Name,
                            "FullControl", "Allow"
                        )
                        $acl.SetAccessRule($rule)
                        Set-Acl -Path $key -AclObject $acl

                        # 2. Normalize key: LF endings, guaranteed trailing newline, no BOM
                        $text = [System.IO.File]::ReadAllText($key) -replace "`r`n", "`n"
                        $text = $text.TrimEnd() + "`n"
                        [System.IO.File]::WriteAllText($key, $text, (New-Object System.Text.UTF8Encoding($false)))

                        # 3. Fail fast with a clear message if the key is still unreadable
                        ssh-keygen -y -f $key | Out-Null
                        if ($LASTEXITCODE -ne 0) { throw "ec2-ssh-key credential is not a valid private key" }

                        ssh -o StrictHostKeyChecking=no -i $key "$($env:SSH_USER)@$($env:EC2_HOST)" "curl -fsS http://localhost:8000/health && curl -fsS http://localhost:8000/readiness"
                        if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE)" }
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