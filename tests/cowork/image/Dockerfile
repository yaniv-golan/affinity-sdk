# Agent image for the xaffinity-cli plugin's cowork-harness tests: the harness base image plus
# the CLI from this repo (wheel built by ../record.sh), wired to a fake Affinity API that serves
# synthetic data. Point COWORK_AGENT_IMAGE at the built tag when recording.
FROM ghcr.io/yaniv-golan/cowork-agent-base:2@sha256:ba8495bdda17ad3f05d67f2942c2baf695676df037a5cc97f7320846af4b3697
USER root
COPY dist/*.whl /tmp/wheels/
RUN pip3 install --no-cache-dir "$(ls /tmp/wheels/*.whl)[cli]" \
    && mv /usr/local/bin/xaffinity /usr/local/bin/xaffinity.real \
    && rm -rf /tmp/wheels
COPY fakeaff/server.py fakeaff/fixtures.json /opt/fakeaff/
COPY xaffinity-wrapper.sh /usr/local/bin/xaffinity
RUN chmod 755 /usr/local/bin/xaffinity && chmod -R a+rX /opt/fakeaff
USER ubuntu
