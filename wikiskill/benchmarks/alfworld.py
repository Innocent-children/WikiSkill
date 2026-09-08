"""ALFWorld text simulation using real TextWorld transitions and admissible actions."""

from __future__ import annotations

import re
from pathlib import Path

from ..agents import AgentError, Conversation, prompt
from ..data import json_text
from .base import Benchmark, asset_path


def open_environment(game_file: Path, max_steps: int, seed: int):
    try:
        import textworld
        import textworld.gym
        from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos
    except ImportError as exc:
        raise RuntimeError("Install ALFWorld support with pip install '.[alfworld]'") from exc
    request = textworld.EnvInfos(won=True, admissible_commands=True, extras=["gamefile"])
    environment_id = textworld.gym.register_games([str(game_file)], request_infos=request,
        batch_size=1, asynchronous=False, max_episode_steps=max_steps,
        wrappers=[AlfredDemangler(shuffle=False), AlfredInfos])
    environment = textworld.gym.make(environment_id)
    environment.seed(seed)
    return environment


class ALFWorld(Benchmark):
    name = "alfworld"
    description = "ALFWorld text-based embodied tasks with admissible environment actions"
    allowed_options = {"max_steps", "history_length", "seed"}

    def validate_task(self, task):
        if not isinstance(task.context.get("game_file"), str):
            raise ValueError("ALFWorld task context must include game_file")
        self.check_asset(task.context["game_file"])
        for name, default in (("max_steps", 50), ("history_length", 10)):
            value = self.options.get(name, default)
            if type(value) is not int or value < 1:
                raise ValueError(f"ALFWorld {name} must be a positive integer")

    def run(self, agent, task, skills, wiki, directory):
        max_steps = self.options.get("max_steps", 50)
        environment = open_environment(asset_path(self.data_root, task.context["game_file"]),
                                       max_steps, self.options.get("seed", 42))
        messages, usage, history = [], [], []
        won, done = False, False
        try:
            observations, infos = environment.reset()
            observation = observations[0]
            for step in range(max_steps):
                recent = history[-self.options.get("history_length", 10):]
                admissible = list(infos.get("admissible_commands", [[]])[0])
                substitutions = {"task_description": task.prompt, "skill_section": agent.skill_section(skills),
                    "step_count": str(step), "history_length": str(len(recent)), "action_history": json_text(recent),
                    "current_step": str(step + 1), "current_observation": observation,
                    "admissible_actions": ", ".join(admissible)}
                system = re.sub(r"\{([a-z_]+)\}", lambda match: substitutions[match[1]], prompt("alfworld"))
                if wiki:
                    system += "\n\nWiki available for this training rollout:\n" + json_text(wiki)
                turn = [{"role": "system", "content": system}, {"role": "user", "content": "Choose your next action."}]
                messages.extend(turn)
                response = agent.model.complete(turn, [])
                messages.append(response.message)
                usage.append(response.usage)
                found = re.findall(r"<action>\s*(.*?)\s*</action>", response.message.get("content") or "", re.DOTALL)
                action = found[-1].strip() if found else ""
                if response.finish_reason == "length":
                    action = ""
                previous = observation
                if action not in admissible:
                    observation = previous + "\nInvalid action: choose one of the listed admissible actions."
                    history.append({"observation": previous, "action": action, "feedback": "invalid_action"})
                    continue
                observations, rewards, dones, infos = environment.step([action])
                observation, done = observations[0], bool(dones[0])
                won = bool(infos.get("won", [False])[0])
                history.append({"observation": previous, "action": action, "feedback": observation,
                                "reward": float(rewards[0]), "done": done, "won": won})
                if won or done:
                    break
            return Conversation(messages, "success" if won else "failure",
                "environment_done" if done or won else "turn_limit", usage,
                details={"won": won, "steps": history, "step_count": len(history)})
        except Exception as exc:
            raise AgentError(str(exc), messages, usage) from exc
        finally:
            environment.close()

    def score(self, prediction, task, conversation):
        return float(conversation.details["won"])
