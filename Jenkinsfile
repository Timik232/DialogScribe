pipeline {
    agent { label 'built-in' }

    options {
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        timestamps()
        timeout(time: 90, unit: 'MINUTES')
    }

    parameters {
        string(name: 'GIT_URL', defaultValue: 'https://github.com/Timik232/DialogScribe.git', description: 'Repository URL or Jenkins-local Git bundle')
        string(name: 'GIT_REF', defaultValue: 'master', description: 'Branch, tag, or commit to verify')
        booleanParam(name: 'RUN_MODEL_TESTS', defaultValue: false, description: 'Dispatch GPU/HuggingFace/model checks to a dedicated labeled executor')
    }

    environment {
        PYTHON_IMAGE = "dialogscribe-ci-python:${BUILD_NUMBER}"
        APP_IMAGE = "dialogscribe-ci-app:${BUILD_NUMBER}"
        PIP_INDEX_URL = 'http://192.168.1.48:3141/root/pypi/+simple/'
        PIP_EXTRA_INDEX_URL = 'https://pypi.org/simple/'
        NPM_CONFIG_REGISTRY = 'https://npm-mirror.gitverse.ru'
        JWT_SECRET = 'ci-test-only-not-a-production-secret-0123456789'
    }

    stages {
        stage('Clean checkout') {
            steps {
                deleteDir()
                sh '''
                    set -eu
                    git clone --no-tags "$GIT_URL" .
                    git checkout --detach "$GIT_REF"
                    mkdir -p .ci-artifacts
                    chmod 0777 .ci-artifacts
                    git rev-parse HEAD > .ci-artifacts/verified-commit.txt
                '''
            }
        }

        stage('Secret scan') {
            steps {
                sh '''
                    docker run --rm \
                        -v "$WORKSPACE:/repo" \
                        zricethezav/gitleaks:v8.24.3 \
                        dir /repo --redact --no-banner \
                        --report-format=json \
                        --report-path=/repo/.ci-artifacts/gitleaks.json
                '''
            }
        }

        stage('Build clean Python test image') {
            steps {
                sh 'docker build --pull --no-cache -f ci/Dockerfile.python -t "$PYTHON_IMAGE" .'
            }
        }

        stage('Audit matrix') {
            steps {
                sh '''
                    docker run --rm \
                        -v "$WORKSPACE:/workspace" -w /workspace \
                        "$PYTHON_IMAGE" \
                        python tools/validate_audit_matrix.py audit/findings.json \
                        | tee .ci-artifacts/audit-matrix.log
                '''
            }
        }

        stage('Python collection') {
            steps {
                sh '''
                    docker run --rm \
                        -e JWT_SECRET \
                        -v "$WORKSPACE:/workspace" -w /workspace \
                        "$PYTHON_IMAGE" \
                        python -m pytest --collect-only -q \
                        -m "not requires_gpu and not requires_hf_token and not requires_model" \
                        > .ci-artifacts/python-collection.log
                '''
            }
        }

        stage('Python baseline tests') {
            steps {
                sh '''
                    docker run --rm \
                        -e JWT_SECRET \
                        -v "$WORKSPACE:/workspace" -w /workspace \
                        "$PYTHON_IMAGE" \
                        python tools/verify_test_baseline.py \
                        --junit .ci-artifacts/python-tests.xml \
                        -m "not requires_gpu and not requires_hf_token and not requires_model"
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, skipMarkingBuildUnstable: true, testResults: '.ci-artifacts/python-tests.xml'
                }
            }
        }

        stage('Ruff (Task 1 scope)') {
            steps {
                sh '''
                    docker run --rm \
                        -v "$WORKSPACE:/workspace" -w /workspace \
                        "$PYTHON_IMAGE" \
                        python -m ruff check \
                        tools/validate_audit_matrix.py \
                        tools/verify_test_baseline.py \
                        tests/test_audit_matrix.py \
                        tests/test_diarization.py \
                        | tee .ci-artifacts/ruff.log
                '''
            }
        }

        stage('Frontend baseline check and build') {
            steps {
                sh '''
                    docker run --rm \
                        -e NPM_CONFIG_REGISTRY \
                        -v "$WORKSPACE/frontend:/app" -w /app \
                        node:20-slim \
                        sh -lc 'npm ci --cache /tmp/npm-cache && npx svelte-check --tsconfig ./tsconfig.json --output machine' \
                        > .ci-artifacts/frontend-check.log || test "$?" -eq 1
                    docker run --rm \
                        -v "$WORKSPACE:/workspace" -w /workspace \
                        "$PYTHON_IMAGE" \
                        python tools/verify_frontend_baseline.py .ci-artifacts/frontend-check.log
                    docker run --rm \
                        -e NPM_CONFIG_REGISTRY \
                        -v "$WORKSPACE/frontend:/app" -w /app \
                        node:20-slim \
                        sh -lc 'npm run build' \
                        | tee .ci-artifacts/frontend-build.log
                '''
            }
        }

        stage('Production Docker build') {
            steps {
                sh 'docker build --pull --no-cache -t "$APP_IMAGE" .'
            }
        }

        stage('GPU and model tests') {
            when {
                beforeAgent true
                expression { params.RUN_MODEL_TESTS }
            }
            agent { label 'gpu && model-tests' }
            steps {
                deleteDir()
                sh '''
                    set -eu
                    git clone --no-tags "$GIT_URL" .
                    git checkout --detach "$GIT_REF"
                '''
                withCredentials([string(credentialsId: 'dialogscribe-hf-token', variable: 'HF_TOKEN')]) {
                    sh '''
                        docker run --rm --gpus all \
                            -e HF_TOKEN \
                            -v "$WORKSPACE:/workspace" -w /workspace \
                            python:3.10-slim \
                            sh -lc 'pip install --no-cache-dir -r requirements.txt -r requirements.dev.txt >/dev/null && python -m pytest -q -m "requires_gpu or requires_hf_token or requires_model"'
                    '''
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts allowEmptyArchive: true, artifacts: '.ci-artifacts/**/*', fingerprint: true
            sh 'docker image rm -f "$PYTHON_IMAGE" "$APP_IMAGE" >/dev/null 2>&1 || true'
            deleteDir()
        }
    }
}
