---
typora-copy-images-to: ./pic
---

# TO DO LIST

## What I have done

### 1.distgpipe training

On CIFAR10 MobilenetV2 training

Traditional quantization is one step, one min per tensor. Multiple quantization has multiple steps and mins

Here are some important data. And I have done some train efficiency tests on dist-gpipe, which speed up the process at least 30%.

| Training method         | Compression method          | Acc%   |
| ----------------------- | --------------------------- | ------ |
| tfs(train from scratch) | No                          | 94.07% |
| tfs(train from scratch) | Quantization16(traditional) | 93.94% |
| tfs(train from scratch) | Prune 0.5                   | 94.02% |
| Finetune                | No                          | 96.03% |
| Finetune                | Quantization16(traditional) | 96.07% |
| Finetune                | Prune 0.5                   | 96.27% |

### 2.Dataparallel tests

I have trained 100epochs here.

On CIFAR10 MobilenetV2 training

| Training method | Compression method                      | Acc%        |
| --------------- | --------------------------------------- | ----------- |
| Finetune        | No                                      | 96.1%       |
| Finetune        | Quantization 16bits                     | 96.0%       |
| Finetune        | Prune0.5                                | 96.1%       |
| Finetune        | Prune 0.2                               | 95.4%       |
| Finetune        | Quantization 11bits                     | 91.3% 84.5% |
| Finetune        | SortQuantization 8bits 8splits          | 95.6%       |
| Finetune        | SortQuantization 8bits 8splits prune0.5 | 95.6%       |
| Finetune        | SortQuantization 4bits 16splits         | 95.1%       |

The reason that quantization 11bits has two acc is that, it's curve first climb quickly like quantization 16bits but suddenly fall to 60% and then climb slowly.

On NLP tasks, for the cola dataset, I use Matthew's correlation. The rte dataset uses **validation acc**.

| Tasks | Training method | Compression method              | Validation_value |
| ----- | --------------- | ------------------------------- | ---------------- |
| Cola  | Sota            | None                            | 0.636            |
| Cola  | Finetune        | None                            | 0.634～0.008     |
| Cola  | Finetune        | Prune 0.5                       | 0.633～0.013     |
| Cola  | Finetune        | Quantization 16                 | 0.632～0.010     |
| Cola  | Finetune        | Quantization 10                 | 0.635~0.014      |
| Cola  | Finetune        | Quantization 8                  | 0.644~0.001      |
| Cola  | Finetune        | Quantization 4                  | 0(acc: 69.1%)    |
| RTE   | Sota            | None                            | 78.9%            |
| RTE   | Finetune        | None                            | 78.4% ~ 0.6%     |
| RTE   | Finetune        | Prune 0.5                       | 79.3%~ 0.7%      |
| RTE   | Finetune        | Quantization 16                 | 78.7% ~ 0.7%     |
| RTE   | Finetune        | Quantization 10                 | 0.783~1.1%       |
| RTE   | Finetune        | Quantization 8                  | 77.5% ~ 0.8%     |
| RTE   | Finetune        | Sort Quantization 6bits 4splits | 79.5% ~0.5%      |
| RTE   | Finetune        | Quantization 4                  | 52.2% ~0.1%      |

## To do

1. Finish multiple quantization in distributed method and run samples of CIFAR10 using MobileNetV2 backend.

   motivation: try this new method on the dist_gpipe system, test how many sets of steps and min are reasonable for **8bits** quantization

2. Fulfill the difference of input and output of quantization functions, record them and analyze.

   Motivation: analyze why the error will increase during small bits quantization training. And How to decrease it? Any specification that could represent the tensors distance that could give a response answer to acc decay dramatically.

3. Finish Roberta classification tasks using (prun ,quant,multiple quant) and analyze the results

   Motivation: tests compression layers on NLP tasks(don't prun nn.Embedding again).

# new method introduce

Haokang_quantization

Here is the pseudocode

```python
class SortQuantization(autograd.Function):
    @staticmethod
    def forward(ctx,input,bits,ratio,partition):
        shape = input.shape
        test = input
        input = input.view(-1)
        mask = torch.zeros(input.shape).to(input.get_device())
        src, index = torch.topk(torch.abs(input), int(ratio * input.shape[0]))
        # index and src to send 
        mask.index_fill_(0, index, 1.0)
        input = input * mask
        src = input.index_select(0,index)
        src1, index1 = torch.sort(src, dim = 0,descending=True)
        index1 = index1.chunk(partition)
        src1 = src1.chunk(partition)
        for i in range(partition):
            min, max = src1[i].min(),src1[i].max()
            if min != max:
                step = (max - min) / (pow(2, bits) - 1)
                temp_src = torch.round((src1[i] - min) / step) - pow(2, bits - 1)
                temp_src = (temp_src + pow(2, bits - 1)) * step + min
            else:
                temp_src = src1[i]
            src.scatter_(0,index1[i],temp_src)
        input.scatter_(0,index,src)
        ctx.mask = mask.view(shape)
        ctx.ratio = ratio
        ctx.bits = bits
        ctx.partition = partition
        input = input.view(shape)
        # if input.get_device() == 0:
        #     print("forward",torch.abs(torch.abs(input) - torch.abs(test)).sum()/torch.abs(test).sum())
        return input
    @staticmethod
    def backward(ctx,grad_backward):
        test = grad_backward
        shape = grad_backward.shape
        grad_backward = grad_backward * ctx.mask
        grad_backward = grad_backward.view(-1)
        index = grad_backward.nonzero()
        index = index.view(-1)
        src = grad_backward.index_select(0,index)
        src = src.view(-1)
        src1, index1 = torch.sort(src, dim = 0,descending=True)
        index1= index1.chunk(ctx.partition)
        src1 = src1.chunk(ctx.partition)
        for i in range(ctx.partition):
            min, max = src1[i].min(),src1[i].max()
            if min != max:
                step = (max - min) / (pow(2, ctx.bits) - 1)
                src_temp = torch.round((src1[i] - min) / step) - pow(2, ctx.bits - 1)
                src_temp = (src_temp + pow(2, ctx.bits - 1)) * step + min
            else:
                src_temp = src1[i]
            src.scatter_(0,index1[i],src_temp)
        grad_backward.scatter_(0,index,src)
        grad_backward = grad_backward.view(shape)
        return grad_backward,None,None,None
```

# comparing to k-means

| Settings                    | Method                                 | Input size    | Time per batch | Acc    |
| --------------------------- | -------------------------------------- | ------------- | -------------- | ------ |
| CIFAR10 MobileNetV2 10epoch | K-means 4bits(20 iter)                 | [16,24,56,56] | 0.66s          | 93.01% |
| CIFAR10 MobileNetV2 10epoch | K-means 4bits(50 iter)                 | [16,24,56,56] | 1.33s          | 93.17% |
| CIFAR10 MobileNetV2 10epoch | Quantization 4bits                     | [16,24,56,56] | 0.10s          | 89.42% |
| CIFAR10 MobileNetV2 10epoch | Sort Quantization 4bits(4splits,2bits) | [16,24,56,56] | 0.10s          | 93.38% |
| CIFAR10 MobileNetV2 10epoch | None                                   | [16,24,56,56] | 0.07s          | 94.21% |



## TEST CODE

```
./tests/dataparallel_test_cv.py
```

