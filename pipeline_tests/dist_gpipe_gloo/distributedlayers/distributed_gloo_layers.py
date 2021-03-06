from torch import autograd
import torch.distributed as dist
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

# used for fit the model parallel of mobilenetv2
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
        out = x.view(x.size(0), -1)
        return out


class FSBRFunction(autograd.Function):
    @staticmethod
    def forward(ctx, input: torch.tensor, send_rank: int, self_rank: int):
        ctx.recv_rank, ctx.rank = send_rank, self_rank
        send = input.cpu()

        ctx.send = send
        dist.isend(send, send_rank)
        return input * 1.0

    @staticmethod
    def backward(ctx, grad_ouput):
        recv_rank, rank = ctx.recv_rank, ctx.rank
        recv = ctx.send
        dist.recv(recv, recv_rank)
        grad_ouput = recv.to(rank)

        return grad_ouput, None, None


class FRBSFunction(autograd.Function):
    @staticmethod
    def forward(ctx, input: torch.tensor, recv_rank: int, rank: int):
        ctx.send_rank = recv_rank
        recv = input.cpu()
        recv = input
        dist.recv(recv, recv_rank)
        input = recv.to(rank)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        send_rank = ctx.send_rank
        send = grad_output.cpu()
        send = grad_output
        dist.isend(send, send_rank)
        return grad_output, None, None
