from __future__ import annotations

from zero_agent import ZeroAgent
import pommerman
import settings
import numpy as np


class Evaluator:
    def __init__(self, best_net, new_net, num_games, num_simulations):
        """
        Initialize the evaluation component
        :param best_net: Network 1
        :param new_net: Network 2
        :param num_games: Number of games played in each evaluation
        :param num_simulations: Number of MCTS simulations to select each move
        """
        self._net_1 = best_net
        self._net_2 = new_net
        self._num_games = num_games
        self._env = pommerman.make(
            settings.game_config_id,
            [
                ZeroAgent(best_net, num_simulations=num_simulations, is_self_play=False, num_exploration_steps=0),
                ZeroAgent(new_net, num_simulations=num_simulations, is_self_play=False, num_exploration_steps=0),
            ]
        )

    def start(self):
        """Start evaluation and return win ratios of two players"""
        win_count = np.zeros(2)
        for i in range(self._num_games):

            state = self._env.reset()
            done = False
            reward = None
            while not done:
                # print('[Evaluation] Step %d' % self._env._step_count)
                actions = self._env.act(state)
                state, reward, done, info = self._env.step([a.value for a in actions])
            if reward[0] == settings.win_reward and reward[1] == settings.lose_reward:
                win_count[0] += 1
            elif reward[1] == settings.win_reward and reward[0] == settings.lose_reward:
                win_count[1] += 1

        return win_count

