from __future__ import annotations
from typing import List, Tuple

from pommerman.agents import BaseAgent
from pommerman.constants import Action
from ast import literal_eval
from sim_agent import SimAgent
from mcts import State, MCTS

import os
import pommerman
import settings
import numpy as np
import json


class ZeroAgent(SimAgent):
    def __init__(self, net, num_simulations, is_self_play, num_exploration_steps, num_processes=None):
        """
        Initialize a MCTSAgent.

        :param num_simulations: The maximal iteration of MCTS
        """
        super(ZeroAgent, self).__init__(create_sim_env=True)

        self._net = net
        self._iteration_limit = num_simulations
        self._num_processes = num_processes
        self._is_self_play = is_self_play
        self._num_exploration_steps = num_exploration_steps

        self._training_states_self = []
        self._action_prs_self = []
        self._training_states_other = []
        self._action_prs_other = []

        # Initialized in init_agent()
        self._game_type = None

    def get_training_data(self):
        return (self._training_states_self, self._action_prs_self), \
               (self._training_states_other, self._action_prs_other)

    def _act(self, obs, action_space):
        state = self._create_sim_state(obs)

        env_state = _EnvState(state, self._character.agent_id, self._sim_env, self._net)

        selected_actions = None
        selected_actions_prs = None
        if self._is_self_play and self._step_count <= self._num_exploration_steps:
            temp = 1.0
        else:
            temp = 1e-3
        searcher = MCTS(env_state, temp=temp, iteration_limit=self._iteration_limit, is_self_play=self._is_self_play)
        for i, (actions, action_prs) in enumerate(searcher.search()):
            if i == self._character.agent_id:
                self._training_states_self += self._get_training_states(i)
                self._action_prs_self.append(action_prs)

                selected_actions = actions
                selected_actions_prs = action_prs
            else:
                self._training_states_other += self._get_training_states(i)
                self._action_prs_other.append(action_prs)

        np.random.seed(int.from_bytes(os.urandom(4), byteorder='little'))
        action = np.random.choice(selected_actions, p=selected_actions_prs)

        return action

    def _get_training_states(self, player_index):
        board_shape = self._board.shape
        states = []

        board = np.array(self._board)
        if player_index == 1:
            _change_roles_of_player(board)

        # Bombs
        bomb_blast_strength = np.array(self._bomb_blast_strength)
        bomb_life = np.array(self._bomb_life)

        # Ability
        ammo = np.zeros(board_shape)
        blast_strength = np.zeros(board_shape)
        can_kick = np.zeros(board_shape)
        for agent in self._agents:
            x, y = agent.pos
            ammo[x][y] = agent.ammo
            blast_strength[x][y] = agent.blast_strength
            can_kick[x][y] = 1 if agent.can_kick else 0

        # Step count
        if self._step_count <= 200:
            step_count = 0
        elif 200 < self._step_count <= 400:
            step_count = 1
        elif 400 < self._step_count <= 600:
            step_count = 2
        else:
            step_count = 3
        step_count = np.full(board_shape, step_count)

        states.append(
            np.concatenate([
                board[None], bomb_blast_strength[None], bomb_life[None], ammo[None],
                blast_strength[None], can_kick[None], step_count[None]
            ])
        )
        return states

    def init_agent(self, id_, game_type):
        super(ZeroAgent, self).init_agent(id_, game_type)
        self._game_type = game_type

    def _make_env_copy(self):
        state = self._sim_env.get_json_info()

        new_env = pommerman.make(
            pommerman.REGISTRY[self._game_type.value],
            [_DummyAgent() for _ in range(2)]
        )
        new_env._init_game_state = state
        new_env.set_json_info()

        return new_env


class _DummyAgent(BaseAgent):
    def act(self, obs, action_space):
        pass


class _EnvState(State):
    def __init__(self, state, agent_id, sim_env, net, rewards=None, done=False):
        super(_EnvState, self).__init__(agent_id, settings.num_agents, reward_type=settings.RewardType,
                                        action_type=Action)
        self._state = state
        self._env = sim_env
        self._agent_id = agent_id
        self._done = done
        self._rewards = rewards
        self._net = net

        self._possible_actions = [(x.value, y.value) for x in Action for y in Action]
        self._possible_actions_for_single_player = [x for x in Action]

    def policy_value(self, noise=False) -> Tuple[List[Tuple[State.ActionTuple, Tuple[float]]], State.RewardTuple]:
        action_prs_for_players = []
        values = []
        for player_index in range(2):
            action_prs, value = self._net.predict(self._process_state(self._state, player_index))
            values.append(value[0, 0])

            # Add noise
            np.random.seed(int.from_bytes(os.urandom(4), byteorder='little'))
            prs_raw = np.array([action_pr[1] for action_pr in action_prs])
            prs_processed = prs_raw * 0.75 + np.random.dirichlet(0.3 * np.ones(len(prs_raw)))
            for i in range(len(action_prs)):
                action_prs[i] = action_prs[i][0], prs_processed[i]

            action_prs_for_players.append(action_prs)
        results = []
        for action_tuple_1 in action_prs_for_players[0]:
            for action_tuple_2 in action_prs_for_players[1]:
                action_tuple = action_tuple_1[0], action_tuple_2[0]
                pr_tuple = action_tuple_1[1], action_tuple_2[1]
                results.append((action_tuple, pr_tuple))
        # noinspection PyTypeChecker
        return results, tuple(values)

    def get_possible_actions_for_single_player(self, player_index: int) -> List[State.ActionType]:
        return self._possible_actions_for_single_player

    def get_possible_actions(self):
        return self._possible_actions

    def take_actions(self, actions: State.ActionTuple) -> State:
        self._update_env()

        _, rewards, done, _ = self._env.step([action.value for action in actions])
        new_state = self._env.get_json_info()

        return _EnvState(new_state, self._agent_id, self._env, self._net, rewards=rewards, done=done)

    def is_terminal(self):
        if self._done is None:
            return False
        return self._done

    def get_rewards(self):
        return self._rewards

    def _update_env(self):
        self._env._init_game_state = self._state
        self._env.set_json_info()
        self._env._intended_actions = [i for i in literal_eval(self._state['intended_actions'])]

    @staticmethod
    def _process_state(raw_sim_state, player_index):
        raw_sim_state = raw_sim_state.copy()
        for key, value in raw_sim_state.items():
            raw_sim_state[key] = json.loads(value)
        board_size = raw_sim_state['board_size']
        board_shape = board_size, board_size
        board = np.array(raw_sim_state['board'])

        if player_index == 1:
            _change_roles_of_player(board)

        # Bombs
        bomb_blast_strength = np.zeros(board_shape)
        bomb_life = np.zeros(board_shape)
        for bomb in raw_sim_state['bombs']:
            x, y = bomb['position']
            bomb_life[x][y] = bomb['life']
            bomb_blast_strength[x][y] = bomb['blast_strength']

        # Ability
        ammo = np.zeros(board_shape)
        blast_strength = np.zeros(board_shape)
        can_kick = np.zeros(board_shape)
        for agent in raw_sim_state['agents']:
            x, y = agent['position']
            ammo[x][y] = agent['ammo']
            blast_strength[x][y] = agent['blast_strength']
            can_kick[x][y] = 1 if agent['can_kick'] else 0

        # Step count
        if raw_sim_state['step_count'] <= 200:
            step_count = 0
        elif 200 < raw_sim_state['step_count'] <= 400:
            step_count = 1
        elif 400 < raw_sim_state['step_count'] <= 600:
            step_count = 2
        else:
            step_count = 3
        step_count = np.full(board_shape, step_count)

        return np.concatenate([
            board[None],
            bomb_blast_strength[None],
            bomb_life[None],
            ammo[None],
            blast_strength[None],
            can_kick[None],
            step_count[None]
        ])[None]


def _change_roles_of_player(board):
    for row in range(len(board)):
        for col in range(len(board[0])):
            if board[row][col] == 10:
                board[row][col] = 11
            elif board[row][col] == 11:
                board[row][col] = 10
