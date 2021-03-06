from torchvision.models import mobilenet_v2
import torch.nn as nn
import torch.nn.functional as F


class Reshape1(nn.Module):
    def __init__(self):
        super(Reshape1, self).__init__()
        pass

    def forward(self, x):
        out = F.relu(x)
        out = F.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        return out


def mobilenet_v2_seperate(args):
    model = model = mobilenet_v2(pretrained=True)
    model.classifier[-1] = nn.Linear(1280, 10)
    feature = model.features[0].children()
    conv = next(feature)
    bn = next(feature)
    if args.convinsert is True:
        conv1 = nn.Conv2d(32, 20, (3, 3), (3, 3))
        t_conv1 = nn.ConvTranspose2d(320, 1280, (1, 1), (1, 1))
        layer1 = [conv, bn, conv1]
        layer3 = [t_conv1, Reshape1(), model.classifier]
    else:
        layer1 = [conv, bn]
        # layer2 = [nn.ReLU6(inplace=False), model.features[1:]]
        layer3 = [Reshape1(), model.classifier]
    layer1 = nn.Sequential(*layer1)
    # layer2 = nn.Sequential(*layer2)
    layer3 = nn.Sequential(*layer3)
    partition = [layer1, layer3]
    return partition
