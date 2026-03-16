from google.adk.agents import Agent

coordinator = Agent(
    name="coordinator",
    model="gemini-2.0-flash",
    description="Root orchestrator. Routes to specialist agents.",
    instruction="You are the coordinator. Route user requests to the 
correct specialist agent. Never analyze directly."
)
