#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Using custome network to classify data sampled from CIFAR10 data.

Network can be customed, see class My_Model for more details about how to custome model,
which will be added with a classification layer.  Note that VGG net will first be
trained with all CIFAR10 dataset for a better classification.
"""

__author__ = "Model_compression"
__copyright__ = "Copyright 2021, Lossless compression"
__license__ = "GPL"
__version__ = "1.0.1"
__email__ = "yongqi_du@hust.edu.cn"
__status__ = "Production"

import sys
import os

sys.path.append(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
import math
from collections import Counter, OrderedDict

import numpy as np
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
import torchvision
import torchvision.transforms as transforms
from torch.utils.data import DataLoader, Dataset
from model_define.model import My_Model
import torch.nn.utils.prune as prune
from vgg_net_cifar10 import VGG

device = "cuda:1" if torch.cuda.is_available() else "cpu"

current_path = os.path.dirname(__file__)
class Feature_Dataset(Dataset):

    def __init__(self, X, Y) -> None:
        """Packed Features extracted from VGG19 feature layer which will
        be further concatenated with random feature layers and classification layers.

        Arguments:
            X -- Features of data
            Y -- Labels of data
        """
        super().__init__()
        self.X, self.Y = X, Y

    def __getitem__(self, idx):
        return self.X[idx, :], self.Y[idx]

    def __len__(self):
        return self.X.shape[0]


if __name__ == '__main__':
    # gpu_usage()
    # ------------------------------Data Preparing------------------------------------
    model_vgg = VGG('VGG19')
    model_vgg.load_state_dict(torch.load('./model_vgg'))

    transform_train = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465),
                             (0.2023, 0.1994, 0.2010)),
    ])

    transform_test = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465),
                             (0.2023, 0.1994, 0.2010)),
    ])

    train_data = torchvision.datasets.CIFAR10(root='./data',
                                              train=True,
                                              download=True,
                                              transform=transform_train)
    trainloader = torch.utils.data.DataLoader(train_data,
                                              batch_size=50000,
                                              shuffle=False)

    test_data = torchvision.datasets.CIFAR10(root='./data',
                                             train=False,
                                             download=True,
                                             transform=transform_test)
    testloader = torch.utils.data.DataLoader(test_data,
                                             batch_size=10000,
                                             shuffle=False)

    classes = ('Airplane', 'Car', 'Bird', 'Cat', 'Deer', 'Dog', 'Frog',
               'Horse', 'Ship', 'Truck')

    # ------------------------------Feature Extration----------------------------------
    train_data, train_label = next(iter(trainloader))
    test_data, test_label = next(iter(testloader))
    with torch.no_grad():
        feature_train = model_vgg.features(train_data)
        feature_train = feature_train.view(feature_train.shape[0], -1)
        feature_test = model_vgg.features(test_data)
        feature_test = feature_test.view(feature_test.shape[0], -1)
        p = feature_train.shape[1]
        N = feature_train.shape[0]
        mean_selected_data = torch.mean(feature_train, dim=0)
        norm2_selected_data = torch.sum(
            (feature_train - mean_selected_data)**2, (0, 1)) / N
        feature_train = feature_train - mean_selected_data
        feature_train = feature_train / np.sqrt(norm2_selected_data)

        p = feature_test.shape[1]
        N = feature_test.shape[0]
        mean_selected_data = torch.mean(feature_test, dim=0)
        norm2_selected_data = torch.sum((feature_test - mean_selected_data)**2,
                                        (0, 1)) / N
        feature_test = feature_test - mean_selected_data
        feature_test = feature_test / np.sqrt(norm2_selected_data)

    # dataset for future training testing
    feature_train_dataset = Feature_Dataset(feature_train, train_label)
    feature_test_dataset = Feature_Dataset(feature_test, test_label)

    tau_zero = torch.sqrt(
        torch.mean(torch.diag(torch.mm(feature_train,
                                       feature_train.t())))).detach().numpy()
    print(tau_zero)
    # -------------------------------- Network Setting---------------------------------
    # origin network setting
    layer_num = 3  # layer number for network
    input_num = 512  # input dimension for network 784/256
    weight_num_list = [400, 400, 200]  # number for neurons for each layer
    # weight_num_list = [1000, 1000, 500]
    # weight_num_list = [3000, 3000, 1000]
    # weight_num_list = [5000, 5000, 2500]
    # weight_num_list = [10000, 10000, 5000]
    activation_list = [
        {
            'name': 'ReLU',
            'args': None
        },
        # {'name' : 'Binary_Zero', 'args' : {'s1':1, 's2': 2, 'b1': 1}},
        # {'name' : 'ReLU', 'args' : None},
        {
            'name': 'ReLU',
            'args': None
        },
        {
            'name': 'ReLU',
            'args': None
        }
    ]
    # {'name' : 'ReLU', 'args' : None}]                               # activation for each layer, if with param, write as Binary_Zero here

    #  define origin model
    model = My_Model(layer_num=layer_num,
                     input_num=input_num,
                     weight_num_list=weight_num_list,
                     activation_list=activation_list,
                     tau_zero=tau_zero)

    model_origin = nn.Sequential(
        OrderedDict([
            ('feature', model),
            ('classification',
             nn.Linear(model.weight_num_list[-1], 10, bias=False)),
            ('activation', nn.Softmax()),
        ]))

    # --------------------------------Model Initilization-------------------------------

    # model initialization
    initialization_way = 'ternary'  # select from ['normal', 'random_sparsity', 'ternary']
    kesi = 0.9  # change from 0 to 1, only used for ['random sparsity', 'ternary']
    threshould = 0.9

    if initialization_way == 'normal':
        # normal initialization
        for fc in model_origin.feature.fc_layers:
            nn.init.normal_(fc.weight)
            fc.weight.requires_grad = False
    elif initialization_way == 'random_sparsity':
        # random sparse gaussian weight(break assumption1)
        for fc in model_origin.feature.fc_layers:
            mask = np.zeros(fc.weight.shape).flatten()
            mask[:round((1 - kesi) * mask.size)] = 1
            np.random.shuffle(mask)
            mask = torch.tensor(mask.reshape(fc.weight.shape)).float()
            nn.init.normal_(fc.weight)
            with torch.no_grad():
                fc.weight = torch.nn.Parameter(mask * fc.weight.data,
                                               requires_grad=False)
    elif initialization_way == 'ternary':
        # tarnary weight with sparsity kesi
        for fc in model_origin.feature.fc_layers:
            init = np.zeros(fc.weight.shape).flatten()
            init[:round(1 / 2 * (1 - kesi) *
                        init.size)] = 1 / np.sqrt(1 - kesi)
            init[round(1 / 2 * (1 - kesi) * init.size):2 *
                 round(1 / 2 *
                       (1 - kesi) * init.size)] = -1 / np.sqrt(1 - kesi)
            # c = Counter(init)
            np.random.shuffle(init)
            init = torch.tensor(init.reshape(fc.weight.shape)).float()
            with torch.no_grad():
                fc.weight = torch.nn.Parameter(init, requires_grad=False)
    elif initialization_way == 'pruning':
        for fc in model_origin.feature.fc_layers:
            nn.init.normal_(fc.weight)
            fc.weight.requires_grad = False

        # pruning magnitude based
        # threshould = 0.95
        for fc in model_origin.feature.fc_layers:
            prune.l1_unstructured(fc, 'weight', amount=threshould)
            fc.weight.requires_grad = False
    # ------------------------------------Preparing-------------------------------------
    net = model_origin
    batch_size = 128
    lr = 0.01
    config = {"save_path": "./model_origin", "early_stop": 20, 'n_epochs': 200}
    # define data
    feature_train_dataloader = DataLoader(feature_train_dataset,
                                          batch_size=batch_size,
                                          shuffle=False)
    feature_test_dataloader = DataLoader(feature_test_dataset,
                                         batch_size=batch_size,
                                         shuffle=False)

    net = net.to(device)
    if device == 'cuda:1':
        #     net = torch.nn.DataParallel(net, device_ids=device_id)
        cudnn.benchmark = True

    epochs, best_loss, step, early_stop_count = config[
        'n_epochs'], math.inf, 0, 0
    optimizer = torch.optim.Adam(net.parameters(), lr=lr)
    # optimizer = torch.optim.SGD(net.parameters(),
    #                             lr=lr,
    #                             momentum=0.9,
    #                             weight_decay=5e-4)
    criterion = nn.CrossEntropyLoss()

    # ------------------------------Training and Validation-----------------------------
    for epoch in range(epochs):
        net.train()
        loss_record = []
        accuracy_record = []
        for train_data, train_label in feature_train_dataloader:
            optimizer.zero_grad()
            # train_data , train_label = train_data.to(device), train_label.to(device)
            # X, y = train_data.to(device), train_label.to(device)
            train_data, train_label = train_data.to(device), train_label.to(
                device)
            pred = net(train_data)
            # print(pred.device)
            # print(train_label.device)
            loss = criterion(pred, train_label)
            loss.backward()
            optimizer.step()
            loss_record.append(loss.item())
            # accuracy
            _, index = pred.data.cpu().topk(1, dim=1)
            index_label = train_label.data.cpu()
            accuracy_batch = np.sum(
                (index.squeeze(dim=1) == index_label).numpy())
            accuracy_batch = accuracy_batch / len(train_label)
            accuracy_record.append(accuracy_batch)
        train_loss = sum(loss_record) / len(loss_record)
        train_accuracy = sum(accuracy_record) / len(accuracy_record)

        # validation
        net.eval()
        loss_record = []
        accuracy_record = []
        for val_data, val_label in feature_test_dataloader:
            # X, y = val_data.to(device), val_label.to(device)
            val_data, val_label = val_data.to(device), val_label.to(device)
            with torch.no_grad():
                pred = net(val_data)
                loss = criterion(pred, val_label)
            loss_record.append(loss.item())
            # accuracy
            _, index = pred.data.cpu().topk(1, dim=1)
            index_label = val_label.data.cpu()
            accuracy_batch = np.sum(
                (index.squeeze(dim=1) == index_label).numpy())
            accuracy_batch = accuracy_batch / len(val_label)
            accuracy_record.append(accuracy_batch)
        val_loss = sum(loss_record) / len(testloader)
        val_accuracy = sum(accuracy_record) / len(accuracy_record)

        print(
            f'Epoch [{epoch+1}/{epochs}]: Train loss: {train_loss:.4f},Train accuracy: {train_accuracy:.4f}, Valid loss: {val_loss:.4f}, Valid accuracy: {val_accuracy:.4f}'
        )

        if val_loss < best_loss:
            best_loss = val_loss
            torch.save(net.state_dict(),
                       config['save_path'])  # Save your best model
            print('Saving model with loss {:.3f}...'.format(best_loss))
            early_stop_count = 0
        else:
            early_stop_count += 1

        if early_stop_count >= config['early_stop']:
            print('\nModel is not improving, so we halt the training session.')
            break
    'here just a test for all binary activation'

    # origin performance
    model_origin_final_accuracy, model_origin_final_loss = val_accuracy, val_loss

    print(
        f'origin model: Valid loss: {model_origin_final_loss:.4f}, Valid accuracy: {model_origin_final_accuracy:.4f}'
    )
