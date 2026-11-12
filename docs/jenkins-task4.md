# Day 2 Jenkins build

Jenkins runs the secondary build path from the same GitLab repository. It checks out the commit, installs dependencies, runs pytest, scans source and dependencies, builds a Docker image and scans it with Trivy. A successful build publishes a downloadable Docker archive. The GitLab pipeline remains the primary deployment configuration.

1. In Jenkins, use a Windows agent with the label `capstone-windows`. From the agent's account, confirm that `git --version`, `python --version` and `docker version` work. Docker Desktop must be running in Linux container mode. Use a dedicated agent for this trusted project because Docker access gives the build control of the local engine.
2. Push the Jenkinsfile to the project. Create a **Pipeline** job in Jenkins. Set **Definition** to **Pipeline script from SCM**, **SCM** to **Git**, repository URL to `https://gitlab.com/straive-group303444/capstone_final.git`, branch to `*/main`, and **Script Path** to `Jenkinsfile`. Add Jenkins SCM credentials if the project requires them.
3. Click **Build Now**. The **Test Result** and build artifacts are the evidence for Task 4. Download the JSON scan reports, pytest XML and `capstone-image.tar` from the build. The image archive is published after the zero-critical gate passes.

Load a downloaded artifact into Docker Desktop with `docker load -i capstone-image.tar`. Use the image name and ID recorded in `image.json` to identify the build.

Jenkins can complete this build without GitLab CI. The Jenkins job still uses GitLab to fetch the repository. The Jenkins build record can demonstrate the secondary path, while the GitLab pipeline record is separate Task 3 evidence.

For a local capstone demo with a dedicated Jenkins instance, open **Manage Jenkins → Nodes → Built-In Node → Configure**. Set **Number of executors** to `1` and add the label `capstone-windows`. Install the suggested plugins during initial setup so the Pipeline, Git and JUnit steps are available.
