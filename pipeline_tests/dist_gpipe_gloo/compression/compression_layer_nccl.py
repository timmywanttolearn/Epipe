"""
Author: your name
Date: 2022-04-03 11:32:55
LastEditTime: 2022-04-08 19:02:58
LastEditors: Please set LastEditors
Description: 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
FilePath: /research/gpipe_test/dist_gpipe/compression/gloo_layer.py
"""

from lib2to3.pgen2 import pgen
from numpy import dtype
import torch.nn.functional as F
from torch import autograd
import torch
import torch.nn as nn
import torch.distributed as dist
import time
from .functions import *


def create_sparse(input: torch.tensor, bit_saving=True):
    shape = input.shape
    input = input.view(-1)
    index = input.nonzero()
    index = index.view(-1)
    if bit_saving is True:
        index = index.type(torch.bfloat16)
    src = input.index_select(0, index)
    input = input.view(shape)
    return shape, index, src


def unzip_sparse(input, index, src, shape):
    input = input.view(-1)
    input.scatter_(0, index, src)
    input = input.view(shape)
    return input


class FastQuantClient(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, send_rank, device, pg=None):
        ctx.recv_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            send_rank,
            bits,
            split_bits,
            pg,
        )
        ctx.device = device
        shape = input.shape
        min_step = torch.zeros([2 ** split_bits, 4])
        # min_step, output = SortQuantization(input, bits, split_bits, min_step)
        min_step, output = FastQuantization(input, bits, split_bits, min_step)
        min_step = min_step.to(device)
        output = output.to(device)
        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output: torch.tensor):
        recv_rank, bits, split_bits, pg = (
            ctx.recv_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        device = ctx.device
        shape = grad_output.shape
        grad_output = grad_output.view(-1)
        # must use the right dtype to recv tensor
        if bits + split_bits <= 8:
            recv = grad_output.type(torch.int8)
        else:
            recv = grad_output.type(torch.int16)
            recv = recv.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2])
        min_step = min_step.to(device)
        recv = recv.to(device)
        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)
        min_step = min_step.cpu()
        recv = recv.cpu()
        grad_output = FastDequantization(recv, bits, split_bits, min_step, grad_output)
        grad_output = grad_output.view(shape)
        # print(grad_output)
        return grad_output, None, None, None, None, None


class FastDequantClient(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, recv_rank, device, pg=None):
        ctx.send_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            recv_rank,
            bits,
            split_bits,
            pg,
        )
        ctx.device = device
        shape = input.shape
        input = input.view(-1)
        if bits + split_bits <= 8:
            recv = input.type(torch.int8)
        else:
            recv = input.type(torch.int16)
            recv = recv.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2])
        min_step = min_step.to(device)
        recv = recv.to(device)
        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)
        min_step = min_step.cpu()
        recv = recv.cpu()
        input = FastDequantization(recv, bits, split_bits, min_step, input)
        # input = input * 1.0
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        send_rank, bits, split_bits, pg = (
            ctx.send_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        device = ctx.device
        shape = grad_output.shape
        min_step = torch.zeros([2 ** split_bits, 4])
        # min_step, output = SortQuantization(grad_output, bits, split_bits, min_step)
        min_step, output = FastQuantization(grad_output, bits, split_bits, min_step)
        min_step = min_step.to(device)
        output = output.to(device)
        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        grad_output = grad_output.view(shape)
        return grad_output, None, None, None, None, None


class FastQuantizationServer(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, send_rank, pg=None):
        ctx.recv_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            send_rank,
            bits,
            split_bits,
            pg,
        )
        shape = input.shape
        min_step = torch.zeros([2 ** split_bits, 2]).to(input.get_device())
        min_step, output = FastQuantization(input, bits, split_bits, min_step)

        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        recv_rank, bits, split_bits, pg = (
            ctx.recv_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        shape = grad_output.shape
        grad_output = grad_output.view(-1)
        # must use the right dtype to recv tensor
        if bits + split_bits <= 8:
            recv = grad_output.type(torch.int8)
        else:
            recv = grad_output.type(torch.int16)
            recv = recv.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2]).to(grad_output.get_device())
        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)
        # should view to int16 since we represent int16 with int8
        # lower_bound = min_step[:, -2].to("cpu").type(torch.int32)
        # upper_bound = min_step[:, -1].to("cpu").type(torch.int32)
        grad_output = FastDequantization(recv, bits, split_bits, min_step, grad_output)
        grad_output = grad_output.view(shape)
        # print("server backward recv")
        # print(grad_output)
        return grad_output, None, None, None, None


class FastDequantizationServer(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, recv_rank, pg=None):
        ctx.send_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            recv_rank,
            bits,
            split_bits,
            pg,
        )
        shape = input.shape
        input = input.view(-1)
        if bits + split_bits <= 8:
            recv = input.type(torch.int8)
        else:
            recv = input.type(torch.int16)
            recv = input.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2]).to(input.get_device())
        # recv = torch.rand([64,32,112,112]).to(input.get_device())
        # TODO change the recving method

        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)

        # input = FastDequantizationCPU(input,bits,split_bits,min_step,recv)
        # print(end - start)
        input = FastDequantization(recv, bits, split_bits, min_step, input)
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        send_rank, bits, split_bits, pg = (
            ctx.send_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        # torch.cuda.synchronize()
        # start = time.time()
        shape = grad_output.shape
        # print(grad_output)

        min_step = torch.zeros([2 ** split_bits, 2]).to(grad_output.get_device())
        min_step, output = FastQuantization(grad_output, bits, split_bits, min_step)

        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        grad_output = grad_output.view(shape)
        # print(grad_output)
        # torch.cuda.synchronize()
        # print(time.time()- start)
        return grad_output, None, None, None, None, None


class TopkPruning(autograd.Function):
    @staticmethod
    def forward(ctx, input, ratio):
        shape = input.shape
        input = input.view(-1)
        src, index = torch.topk(torch.abs(input), int(ratio * input.shape[0]))
        mask = torch.zeros(input.shape).to(input.get_device())
        mask.index_fill_(0, index, 1.0)
        input = input * mask
        mask = mask.view(shape)
        ctx.mask = mask
        input = input.view(shape)
        return input * 1.0

    @staticmethod
    def backward(ctx, grad_output):

        return grad_output * ctx.mask, None, None


class TopkLayer(nn.Module):
    def __init__(self, compress_ratio, shape):
        super(TopkLayer, self).__init__()
        self.ratio = compress_ratio
        # self.mask = torch.zeros(shape)

    def forward(self, x):
        return TopkPruning.apply(x, self.ratio)


def q10toint32():
    pass


def q12toint32():
    pass


def QuantizationGPU(input, bits):

    min, max = input.min(), input.max()
    step = (max - min) / (pow(2, bits) - 1)
    output = torch.round((input - min) / step - pow(2, bits - 1))
    # print("quant 16bits",output)
    if bits <= 8:
        output = output.type(torch.int8)
    # elif bits == 10:
    #     output = q10toint32()
    # elif bits == 12:
    #     output = q12toint32()#TODO
    elif bits <= 16:
        output = output.type(torch.int16)
        output = output.view(dtype=torch.int8)
    min = min.to(input.get_device())
    step = step.to(input.get_device())
    return output, min, step


def QtensorSendonGPU(input, min, step, send_rank, pg=None):

    dist.isend(min, send_rank, group=pg)
    dist.isend(step, send_rank, group=pg)
    dist.isend(input, send_rank, group=pg)


def QtensorRecvonGPU1(input, bits, min, step, recv_rank, pg=None):
    # print(input.shape)
    # if bits <= 8:
    #     input =input.type(torch.int8)
    # # elif bits == 10:
    # #     input = int32to10()TODO
    # # elif bits == 12:
    # #     input = int32to12()
    # elif bits <= 16:
    #     input =input.type(torch.int16)
    #     input = input.view(dtype = torch.int8)
    # print(pg)
    dist.recv(min, recv_rank, group=pg)
    dist.recv(step, recv_rank, group=pg)
    dist.recv(input, recv_rank, group=pg)
    return min, step, input


def DequantizationonGPU(input, bits, min, step):
    # print("recv 16",input)
    output = (
        (input.type(torch.float32) + pow(2, bits - 1)) * step + min
    ).requires_grad_()
    return output


class QSendGPU(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, send_rank, rank, pg=None):
        ctx.bits, ctx.recv_rank, ctx.rank, ctx.pg = (bits, send_rank, rank, pg)
        (output, min, step,) = QuantizationGPU(input, bits)
        # print("quant send",output)
        ctx.min, ctx.step = min, step
        ctx.input = output
        # print("pre send to",send_rank )
        QtensorSendonGPU(output, min, step, send_rank, pg)
        # print("send")

        # print("input",input.shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        bits, recv_rank, rank, min, step, pg = (
            ctx.bits,
            ctx.recv_rank,
            ctx.rank,
            ctx.min,
            ctx.step,
            ctx.pg,
        )
        input = ctx.input

        min, step, input = QtensorRecvonGPU1(input, bits, min, step, recv_rank, pg)
        # print("backward recv",input,min,step)
        if bits <= 16 and bits > 8:
            input = input.view(dtype=torch.int16)

        grad_output = DequantizationonGPU(input, bits, min, step)
        # print(grad_output)
        # print(grad_output.shape)
        return grad_output, None, None, None, None


class QSendLayerGPU(nn.Module):
    def __init__(self, bits, send_rank, rank, pg_group, sparse=False) -> None:
        super(QSendLayerGPU, self).__init__()
        self.bits = bits
        self.send_rank = send_rank
        self.rank = rank
        self.sparse = sparse
        self.pg_group = pg_group

    def forward(self, input):

        return QSendGPU.apply(input, self.bits, self.send_rank, self.rank)


def int32to10():
    pass


def int32to12():
    pass


def QtensorRecvonGPU(input, bits, min, step, recv_rank, pg=None):
    # print(input.shape)
    if bits <= 8:
        input = input.type(torch.int8)
    # elif bits == 10:
    #     input = int32to10()TODO
    # elif bits == 12:
    #     input = int32to12()
    elif bits <= 16:
        input = input.type(torch.int16)
        input = input.view(dtype=torch.int8)
    dist.recv(min, recv_rank, group=pg)
    dist.recv(step, recv_rank, group=pg)
    dist.recv(input, recv_rank, group=pg)
    return min, step, input


class QrecvGPU(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, recv_rank, rank, pg=None):
        min = torch.tensor(0.0).to(rank)
        step = torch.tensor(0.0).to(rank)
        ctx.bits, ctx.send_rank, ctx.min, ctx.step, ctx.pg = (
            bits,
            recv_rank,
            min,
            step,
            pg,
        )
        min, step, recv = QtensorRecvonGPU(input, bits, min, step, recv_rank, pg)
        # print("recv quant",recv)
        if bits <= 16 and bits > 8:
            recv = recv.view(dtype=torch.int16)
        input = DequantizationonGPU(recv, bits, min, step)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        bits, send_rank, min, step, pg = (
            ctx.bits,
            ctx.send_rank,
            ctx.min,
            ctx.step,
            ctx.pg,
        )
        output, min, step = QuantizationGPU(grad_output, bits)
        # dist.send(min_step.cpu(), recv_rank)
        # dist.send(output.cpu(), recv_rank)
        QtensorSendonGPU(output, min, step, send_rank, pg)
        # print("backward send",output,min,step)
        return grad_output, None, None, None, None


class QRecvLayerGPU(nn.Module):
    def __init__(self, bits, recv_rank, rank, pg_group) -> None:
        super(QRecvLayerGPU, self).__init__()
        self.bits = bits
        self.recv_rank = recv_rank
        self.rank = rank
        self.min_step = torch.tensor([0.0, 0.0])
        self.pg_group = pg_group

    def forward(self, input):
        return QrecvGPU.apply(input, self.bits, self.recv_rank, self.rank)


# no sparse
class SortQuantGPU(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, send_rank, pg=None):
        ctx.recv_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            send_rank,
            bits,
            split_bits,
            pg,
        )
        shape = input.shape
        min_step = torch.zeros([2 ** split_bits, 2]).to(input.get_device())

        min_step, output = SortQuantization(input, bits, split_bits, min_step)
        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        recv_rank, bits, split_bits, pg = (
            ctx.recv_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        shape = grad_output.shape
        grad_output = grad_output.view(-1)
        # must use the right dtype to recv tensor
        if bits + split_bits <= 8:
            recv = grad_output.type(torch.int8)
        else:
            recv = grad_output.type(torch.int16)
            recv = recv.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2]).to(grad_output.get_device())
        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)
        # should view to int16 since we represent int16 with int8
        grad_output = SortDeQuantization(recv, bits, split_bits, min_step, grad_output)
        grad_output = grad_output.view(shape)
        return grad_output, None, None, None, None


class SortDeQuantGPU(autograd.Function):
    @staticmethod
    def forward(ctx, input, bits, split_bits, recv_rank, pg=None, time_count=False):
        ctx.send_rank, ctx.bits, ctx.split_bits, ctx.pg = (
            recv_rank,
            bits,
            split_bits,
            pg,
        )
        shape = input.shape
        input = input.view(-1)
        if bits + split_bits <= 8:
            recv = input.type(torch.int8)
        else:
            recv = input.type(torch.int16)
            recv = recv.view(torch.int8)

        min_step = torch.zeros([2 ** split_bits, 2]).to(input.get_device())
        ctx.min_step = min_step
        dist.recv(min_step, recv_rank, group=pg)
        dist.recv(recv, recv_rank, group=pg)
        input = SortDeQuantization(recv, bits, split_bits, min_step, input)
        input = input.view(shape)
        return input

    @staticmethod
    def backward(ctx, grad_output):
        send_rank, bits, split_bits, pg = (
            ctx.send_rank,
            ctx.bits,
            ctx.split_bits,
            ctx.pg,
        )
        shape = grad_output.shape
        min_step = torch.zeros([2 ** split_bits, 2]).to(grad_output.get_device())
        min_step, output = SortQuantization(grad_output, bits, split_bits, min_step)
        dist.isend(min_step, send_rank, group=pg)
        dist.isend(output, send_rank, group=pg)
        grad_output = grad_output.view(shape)
        # print(grad_output.shape)
        return grad_output, None, None, None, None, None


# forward svd,backward sortquant
