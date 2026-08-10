I want to take the 4thealth application that is located at ~/code/github/web/4thealth and the 4tAnalyst application at ~/code/github/ai/4tanalyst and merge some of the services together. 

4THealth will be the main functionality and will eventually run in a docker container or on a linux based server (redhat, ubuntu, etc.). That tab around "rule validation" will have an AI functionality from the 4tanalyst but it will utilize API calls against the engineers AI of choice (Codex, Claude, Ollama local or cloud, etc.). That means the server will have the details present for API calls but will need the details (commented out), depending on which one they will use (Claude should be default)

So, the engineer would enter in the same information as if they ran the different skill commands in 4tanalyst and the program will connect to the FMG server after it connects to the LLM over an API call. Not sure if we need an MCP server locally to help with this on the same server.

The plan is to take the 4thealth repo (it will be called 4thealth+). You will need to come up with a plan that will rebuild the files and apps in this folder ~/code/github/ai/4thealth-plus without grabbing anything that doesn't make sense. Make sure to update the doc files, the readme.md file and start with a new changlog.md file. 

Ask questions. I have feeling that this will require multiple sessions due to token limits so this should be well phased. 

Make sure to create a .gitignore with the appropriate entries. I will not upload to a github repo until this is working and we make sure it's ready for the public.