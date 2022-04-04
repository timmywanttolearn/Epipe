'''
Author: your name
Date: 2022-04-03 11:39:13
LastEditTime: 2022-04-03 12:21:42
LastEditors: Please set LastEditors
Description: 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
FilePath: /research/gpipe_test/dist_gpipe_gloo/distributedlayers/distributed_gloo_layer.py
'''
'''
Author: your name
Date: 2022-04-03 11:34:27
LastEditTime: 2022-04-03 11:37:20
LastEditors: Please set LastEditors
Description: 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
FilePath: /research/gpipe_test/dist_gpipe/distributedlayers/distributed_gloo_layer.py
'''
from torch import autograd
import torch.distributed as dist
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

class Reshape1(nn.Module):
    def __init__(self):
        super(Reshape1, self).__init__()
        pass

    def forward(self, x):
        out = F.relu(x)
        out = F.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        return out
class Reshape2(nn.Module):
    def __init__(self):
        super(Reshape2, self).__init__()
        pass

    def forward(self, x):
        # out = F.relu(x)
        # out = F.avg_pool2d(out, 4)
        out = x.view(x.size(0), -1)
        return out


        
class FSBRLayer(autograd.Function):
    @staticmethod
    def forward(ctx, input: torch.tensor, send_rank: int, self_rank: int):
        ctx.recv_rank,ctx.ank =send_rank,self_rank
        send = input.cpu()
        ctx.send = send
        dist.isend(send, send_rank)
        return input * 1.0
    @staticmethod
    def backward(ctx,grad_ouput):
        recv_rank,rank = ctx.recv_rank,ctx.rank
        recv = ctx.send
        dist.recv(recv, recv_rank)
        grad_ouput = recv.to(rank)
        return grad_ouput


class FRBSLayer(autograd.Function):
    @staticmethod
    def forward(ctx, input: torch.tensor, recv_rank: int, rank:int):
        ctx.send_rank = recv_rank
        recv = input.cpu()
        dist.recv(recv,recv_rank)
        input = recv.to(rank)
        return input
    @staticmethod
    def backward(ctx, grad_output):
        send_rank = ctx.send_rank
        send = grad_output.cpu()
        dist.isend(send,send_rank)
        return grad_output
