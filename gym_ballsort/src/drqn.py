from collections import deque
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import random
from torch.autograd import Variable
import numpy as np
import math
import gym
import gym_ballsort.envs
import wandb

class ReplayMemory(object):
    def __init__(self, max_epi_num=50, max_epi_len=300):
        # capacity is the maximum number of episodes
        self.max_epi_num = max_epi_num
        self.max_epi_len = max_epi_len
        self.memory = deque(maxlen=self.max_epi_num)
        self.is_av = False
        self.current_epi = 0
        self.memory.append([])

    def reset(self):
        self.current_epi = 0
        self.memory.clear()
        self.memory.append([])

    def create_new_epi(self):
        self.memory.append([])
        self.current_epi = self.current_epi + 1
        if self.current_epi > self.max_epi_num - 1:
            self.current_epi = self.max_epi_num - 1

    def remember(self, state, action, reward):
        if len(self.memory[self.current_epi]) < self.max_epi_len:
            self.memory[self.current_epi].append([state, action, reward])

    def sample(self):
        epi_index = random.randint(0, len(self.memory)-2)
        if self.is_available():
            return self.memory[epi_index]
        else:
            return []

    def size(self):
        return len(self.memory)

    def is_available(self):
        self.is_av = True
        if len(self.memory) <= 1:
            self.is_av = False
        return self.is_av

    def print_info(self):
        for i in range(len(self.memory)):
            print('epi', i, 'length', len(self.memory[i]))

class Flatten(nn.Module):
    def forward(self, input):
        return input.view(input.size(0), -1)


class DRQN(nn.Module):
    def __init__(self, N_action):
        super(DRQN, self).__init__()
        self.lstm_i_dim = 36   # input dimension of LSTM
        self.lstm_h_dim = 5     # output dimension of LSTM
        self.lstm_N_layer = 1   # number of layers of LSTM
        self.N_action = N_action
        #self.conv1 = nn.Conv2d(in_channels=3, out_channels=16, kernel_size=3, stride=1)
        self.flat1 = Flatten()
        self.lstm = nn.LSTM(input_size=self.lstm_i_dim, hidden_size=self.lstm_h_dim, num_layers=self.lstm_N_layer)
        self.fc1 = nn.Linear(self.lstm_h_dim, 32)
        self.fc2 = nn.Linear(32, self.N_action)

    def forward(self, x, hidden):
        if torch.is_tensor(x):
            h2 = torch.flatten(x, start_dim=1).unsqueeze(1)
        else:
            h2 = torch.flatten(torch.FloatTensor(x)).unsqueeze(0).unsqueeze(0)

        h3, new_hidden = self.lstm(h2, hidden)
        h4 = F.relu(self.fc1(h3))
        h5 = self.fc2(h4)
        return h5, new_hidden


class Agent(object):
    def __init__(self, N_action, max_epi_num=50, max_epi_len=300):
        self.N_action = N_action
        self.max_epi_num = max_epi_num
        self.max_epi_len = max_epi_len
        self.drqn = DRQN(self.N_action)
        self.buffer = ReplayMemory(max_epi_num=self.max_epi_num, max_epi_len=self.max_epi_len)
        self.gamma = 0.9
        self.loss_fn = torch.nn.MSELoss()
        self.optimizer = torch.optim.Adam(self.drqn.parameters(), lr=1e-3)

    def remember(self, state, action, reward):
        self.buffer.remember(state, action, reward)

    """
    def img_to_tensor(self, img):
        img_tensor = torch.FloatTensor(img)
        img_tensor = img_tensor.permute(2, 0, 1)
        return img_tensor

    def img_list_to_batch(self, x):
        # transform a list of image to a batch of tensor [batch size, input channel, width, height]
        temp_batch = self.img_to_tensor(x[0])
        temp_batch = temp_batch.unsqueeze(0)
        for i in range(1, len(x)):
            img = self.img_to_tensor(x[i])
            img = img.unsqueeze(0)
            temp_batch = torch.cat([temp_batch, img], dim=0)
        return temp_batch
    """

    def train(self):
        if self.buffer.is_available():
            memo = self.buffer.sample()
            obs_list = []
            action_list = []
            reward_list = []
            for i in range(len(memo)):
                obs_list.append(memo[i][0])
                action_list.append(memo[i][1])
                reward_list.append(memo[i][2])
            #obs_list = self.img_list_to_batch(obs_list)
            obs_list = torch.FloatTensor(np.array(obs_list))            
            hidden = (Variable(torch.zeros(1, 1, 5).float()), Variable(torch.zeros(1, 1, 5).float()))

            Q, hidden = self.drqn.forward(obs_list, hidden)
            Q_est = Q.clone()
            
            for t in range(len(memo) - 1):
                max_next_q = torch.max(Q_est[t+1, 0, :]).clone().detach()
                Q_est[t, 0, action_list[t]] = reward_list[t] + self.gamma * max_next_q
            T = len(memo) - 1
            Q_est[T, 0, action_list[T]] = reward_list[T]

            loss = self.loss_fn(Q, Q_est)
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
            return loss.detach().numpy()

    def get_action(self, obs, hidden, epsilon):
        if random.random() > epsilon:
            q, new_hidden = self.drqn.forward(obs, hidden)
            action = q[0].max(1)[1].data[0].item()
        else:
            q, new_hidden = self.drqn.forward(obs, hidden)
            action = random.randint(0, self.N_action-1)
        return action, new_hidden

def get_decay(epi_iter):
    decay = math.pow(0.999, epi_iter)
    if decay < 0.05:
        decay = 0.05
    return decay

if __name__ == '__main__':
    # Initialize wandb run
    wandb.finish() # execute to avoid overlapping runnings (advice: later remove duplicates in wandb)
    wandb.init(project="ballsort")

    random.seed()
    env = gym.make("ballsort-v0")
    max_epi_iter = 100000
    max_MC_iter = 3000
    agent = Agent(N_action=81, max_epi_num=5000, max_epi_len=max_MC_iter)
    train_curve = []
    losses = []

    for epi_iter in range(max_epi_iter):
        running_reward = 0
        obs = env.reset()
        hidden = (Variable(torch.zeros(1, 1, 5).float()), Variable(torch.zeros(1, 1, 5).float()))
        for MC_iter in range(max_MC_iter):
            # env.render()
            action, hidden = agent.get_action(obs, hidden, get_decay(epi_iter))
            next_obs, reward, done, info = env.step(action)
            running_reward += reward
            agent.remember(obs, action, reward)
            if done or MC_iter == max_MC_iter-1:
                agent.buffer.create_new_epi()
                break
        print('Episode', epi_iter, 'reward', running_reward)
        train_curve.append(running_reward)
        
        if agent.buffer.is_available():
            loss = agent.train()
            losses.append(loss)
        
        wandb.log(
        {
        'loss': np.mean(losses),
        'ep_reward_reward': running_reward,
        })
    

    wandb.finish()
    np.save("DRQN5_1e3.npy", np.array(train_curve))