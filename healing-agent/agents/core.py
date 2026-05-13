from abc import ABC, abstractmethod
from typing import Dict, List, Type


class BaseAgent(ABC):
    name: str = "base_agent"
    description: str = "Abstract base agent."

    @abstractmethod
    def build_graph(self): ...

    @abstractmethod
    def run(self) -> None: ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


class AgentRegistry:
    _agents: Dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, agent_class: Type[BaseAgent]) -> Type[BaseAgent]:
        cls._agents[agent_class.name] = agent_class
        return agent_class

    @classmethod
    def get(cls, name: str) -> Type[BaseAgent]:
        if name not in cls._agents:
            raise KeyError(f"Agent '{name}' not registered. Available: {cls.list()}")
        return cls._agents[name]

    @classmethod
    def list(cls) -> List[str]:
        return list(cls._agents.keys())
