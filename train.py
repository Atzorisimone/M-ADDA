import pandas as pd
import misc as ms
import test

import torch
import torch.nn as nn
import losses
from sklearn.cluster import KMeans
from torchvision import transforms
from utils import visdom_util
from dataset_utils import get_coxs2v_testset, get_coxs2v_trainset
import utils



fold_tool = utils.FoldGenerator(10, 2, 1)
train_folds, val_folds, test_folds = fold_tool.get_fold()


train_folds = [3,4,5,6,7,8,9]
test_folds = [3,4,5,6,7,8,9]
eval_folds = [3,4,5,6,7,8,9]
people_per_batch = 20
images_per_person = 5


def train(exp_dict):
    history = ms.load_history(exp_dict)

    #Simone:
    data_transform = transforms.Compose([
        transforms.Resize((exp_dict['image_size'], exp_dict['image_size']), interpolation=1),
        transforms.ToTensor()
    ])

    # CUDA for PyTorch
    use_cuda = torch.cuda.is_available()
    device = torch.device("cuda:0" if use_cuda else "cpu")

    history = ms.load_history(exp_dict)


    src_trainloader = get_coxs2v_trainset(exp_dict["still_dir"],
                                          exp_dict["video1_dir"],
                                          exp_dict["video1_pairs"],
                                          train_folds,
                                          exp_dict["cross_validation_num_fold"],
                                          data_transform,
                                          people_per_batch,
                                          images_per_person,
                                          video_only=False,
                                          samples_division_list=[0.6, 0.4],  # [0.6, 0.4]
                                          div_idx=0)

    src_valloader = get_coxs2v_trainset(exp_dict["still_dir"],
                                          exp_dict["video1_dir"],
                                          exp_dict["video1_pairs"],
                                          train_folds,
                                          exp_dict["cross_validation_num_fold"],
                                          data_transform,
                                          people_per_batch,
                                          images_per_person,
                                          video_only=False,
                                          samples_division_list=[0.6, 0.4],  # [0.6, 0.4]
                                          div_idx=1) # to change


    # Source
    #src_trainloader, src_valloader = ms.load_src_loaders(exp_dict)






    ####################### 1. Train source model
    src_model, src_opt = ms.load_model_src(exp_dict)


    # Train Source
    history = fit_source(src_model, src_opt, src_trainloader, history,
                         exp_dict)
    # Test Source
    src_acc = test.validate(src_model, src_model, src_trainloader,
                            src_valloader)

    print("{} TEST Accuracy = {:2%}\n".format(exp_dict["src_dataset"],
                                              src_acc))
    history["src_acc"] = src_acc

    ms.save_model_src(exp_dict, history, src_model, src_opt)

    ####################### 2. Train target model

    #tgt_trainloader, tgt_valloader = ms.load_tgt_loaders(exp_dict)

    tgt_trainloader = get_coxs2v_trainset(exp_dict["still_dir"],
                                          exp_dict["video2_dir"],
                                          exp_dict["video2_pairs"],
                                          train_folds,
                                          exp_dict["cross_validation_num_fold"],
                                          data_transform,
                                          people_per_batch,
                                          images_per_person,
                                          video_only=False,
                                          samples_division_list=[0.6, 0.4],  # [0.6, 0.4]
                                          div_idx=0)

    tgt_valloader = get_coxs2v_trainset(exp_dict["still_dir"],
                                          exp_dict["video2_dir"],
                                          exp_dict["video2_pairs"],
                                          train_folds,
                                          exp_dict["cross_validation_num_fold"],
                                          data_transform,
                                          people_per_batch,
                                          images_per_person,
                                          video_only=False,
                                          samples_division_list=[0.6, 0.4],  # [0.6, 0.4]
                                          div_idx=1) #to change

    print('quello che aggiunto funziona')
    # load models
    tgt_model, tgt_opt, disc_model, disc_opt = ms.load_model_tgt(exp_dict)
    tgt_model.load_state_dict(src_model.state_dict())



    history = fit_target(src_model, tgt_model, tgt_opt, disc_model, disc_opt,
                         src_trainloader, tgt_trainloader, tgt_valloader,
                         history, exp_dict)



    ms.save_model_tgt(exp_dict, history, tgt_model, tgt_opt, disc_model,
                      disc_opt)

    exp_dict["reset_src"] = 0
    exp_dict["reset_tgt"] = 0
    ms.test_latest_model(exp_dict)



def fit_source(src_model, src_opt, src_trainloader, history, exp_dict):
    # Train Source
    #print( range(history["src_train"][-1]["epoch"], exp_dict["src_epochs"]))
    for e in range(history["src_train"][-1]["epoch"], exp_dict["src_epochs"]):
        loss_sum = 0.
        for step, (images, labels) in enumerate(src_trainloader):
            # make images and labels variable
            images = images.cuda()
            labels = labels.squeeze_().cuda()

            # zero gradients for opt
            src_opt.zero_grad()

            # compute loss for critic
            loss = losses.triplet_loss(src_model, {"X": images, "y": labels})

            loss_sum += loss.item()

            # optimize source classifier
            loss.backward()
            src_opt.step()

        loss = loss_sum / step
        print("Source ({}) - Epoch [{}/{}] - loss={:.2f}".format(
            type(src_trainloader).__name__, e, exp_dict["src_epochs"], loss))

        visdom_util.plotter.plot('loss', 'train', 'Class Loss', e, loss)

        history["src_train"] += [{"loss": loss, "epoch": e}]

        if e % 50 == 0:
            ms.save_model_src(exp_dict, history, src_model, src_opt)

    return history


def fit_target(src_model, tgt_model, tgt_opt, disc_model, disc_opt,
               src_trainloader, tgt_trainloader, tgt_valloader, history,
               exp_dict):
    print(range(history["tgt_train"][-1]["epoch"], exp_dict["tgt_epochs"]))

    for e in range(history["tgt_train"][-1]["epoch"],
                   exp_dict["tgt_epochs"] + 1):


        # 1. Train disc
        if exp_dict["options"]["disc"] == True:
            loss_tgt, loss_disc = fit_discriminator(
                src_model,
                tgt_model,
                disc_model,
                src_trainloader,
                tgt_trainloader,
                opt_tgt=tgt_opt,
                opt_disc=disc_opt,
                epochs=3,
                verbose=1)
            visdom_util.plotter.plot('tgt_loss', 'tgt_loss', 'Target Loss', e, loss_tgt)
            visdom_util.plotter.plot('disc_loss', 'disc_loss', 'Discriminator Loss', e, loss_disc)


        acc_tgt = test.validate(src_model, tgt_model, src_trainloader,
                                tgt_valloader)



        history["tgt_train"] += [{
            "epoch":
            e,
            "acc_src":
            history["src_acc"],
            "acc_tgt":
            acc_tgt,
            "n_train - " + exp_dict["src_dataset"]:
            len(src_trainloader.dataset),
            "n_train - " + exp_dict["tgt_dataset"]:
            len(tgt_trainloader.dataset),
            "n_test - " + exp_dict["tgt_dataset"]:
            len(tgt_valloader.dataset)
        }]

        print("\n>>> Methods: {} - Source: {} -> Target: {}".format(
            None, exp_dict["src_dataset"], exp_dict["tgt_dataset"]))
        print(pd.DataFrame([history["tgt_train"][-1]]))

        if (e % 5) == 0:
            ms.save_model_tgt(exp_dict, history, tgt_model, tgt_opt,
                              disc_model, disc_opt)
            #ms.test_latest_model(exp_dict)

        # 2. Train center-magnet
        if exp_dict["options"]["center"] == True:
            fit_center(
                src_model,
                tgt_model,
                src_trainloader,
                tgt_trainloader,
                tgt_opt,
                epochs=1)

    return history


def fit_discriminator(src_model,
                      tgt_model,
                      disc,
                      src_loader,
                      tgt_loader,
                      opt_tgt,
                      opt_disc,
                      epochs=200,
                      verbose=1):
    tgt_model.train()
    disc.train()

    # setup criterion and opt
    criterion = nn.CrossEntropyLoss()

    ####################
    # 2. train network #
    ####################
    disc_loss  = 0.0
    tgt_loss = 0.0
    for epoch in range(epochs):
        # zip source and target data pair
        discLoss = 0.0
        tgtLoss = 0.0

        data_zip = enumerate(zip(src_loader, tgt_loader))
        for step, ((images_src, _), (images_tgt, _)) in data_zip:
            ###########################
            # 2.1 train discriminator #
            ###########################

            # make images variable
            images_src = images_src.cuda()
            images_tgt = images_tgt.cuda()


            # zero gradients for opt
            opt_disc.zero_grad()

            # extract and concat features
            feat_src = src_model.forward(images_src)
            feat_tgt = tgt_model.forward(images_tgt)
            feat_concat = torch.cat((feat_src, feat_tgt), 0)


            # predict on discriminator
            pred_concat = disc(feat_concat.detach())

            # prepare real and fake label
            label_src = torch.ones(feat_src.size(0)).long()
            label_tgt = torch.zeros(feat_tgt.size(0)).long()
            label_concat = torch.cat((label_src, label_tgt), 0).cuda()

            # compute loss for disc

                #print(pred_concat.size())
                #print(label_concat.size())

                # label_concat = label_concat.to(torch.device('cpu'))
                # label_concat = transform.resize(label_concat.numpy(), (1, 2))
                # #label_concat = np.reshape(label_concat, (1, 2))
                # label_concat = torch.from_numpy(label_concat).to('cuda')

                #print(label_concat.size())

            loss_disc = criterion(pred_concat, label_concat)
            loss_disc.backward()

            discLoss += loss_disc.item()


            # optimize disc
            opt_disc.step()

            pred_cls = torch.squeeze(pred_concat.max(1)[1])
            acc = (pred_cls == label_concat).float().mean()


            ############################
            # 2.2 train target encoder #
            ############################

            # zero gradients for opt
            opt_disc.zero_grad()
            opt_tgt.zero_grad()

            # extract and target features

           #feat_tgt = tgt_model.extract_features(images_tgt)

            feat_tgt = tgt_model.forward(images_tgt)

          # predict on discriminator

            pred_tgt = disc(feat_tgt)

            # prepare fake labels

            label_tgt = torch.ones(feat_tgt.size(0)).long().cuda()

            # compute loss for target encoder

            loss_tgt = criterion(pred_tgt, label_tgt)
            tgtLoss += loss_tgt.item()
            loss_tgt.backward()


            # optimize target encoder
            opt_tgt.step()
            
            
            
            #pdb.set_trace()

            #######################
            # 2.3 print step info #
            #######################
            if verbose and ((step + 1) % 40 == 0):
                print("Epoch [{}/{}] - d_loss={:.5f} g_loss={:.5f} acc={:.5f}".format(epoch + 1, epochs, loss_disc.item(), loss_tgt.item(), acc.item()))

        disc_loss += discLoss / step
        tgt_loss += tgtLoss / step
    return disc_loss / epochs, tgt_loss / epochs
    #visdom_util.plotter.plot('acc', 'disc_acc', 'Accuracy', epoch, acc)



def fit_center(src_model,
               tgt_model,
               src_loader,
               tgt_loader,
               opt_tgt,
               epochs=30):
    """Train classifier for source domain."""
    ####################
    # 1. setup network #
    ####################
    #print(type(tgt_model.last.bias.size()[0]))
    n_classes = tgt_model.last.bias.size()[0]





    # set train state for Dropout and BN layers
    src_model.train()
    tgt_model.train()


    src_embeddings, _ = losses.extract_embeddings(src_model, src_loader)



    src_kmeans = KMeans(n_clusters=n_classes)
    src_kmeans.fit(src_embeddings)

    #src_centers = torch.FloatTensor(src_kmeans.means_).cuda()
    src_centers = torch.FloatTensor(src_kmeans.cluster_centers_).cuda()

    ####################

    # 2. Doing Domain Adaptation#
    ####################

    for epoch in range(epochs):
        for step, (images, labels) in enumerate(tgt_loader):
            # make images and labels variable
            images = images.cuda()
            labels = labels.squeeze_().cuda()

            # zero gradients for opt
            opt_tgt.zero_grad()

            # compute loss for critic
            loss = losses.center_loss(tgt_model, {
                "X": images,
                "y": labels
            }, src_model, src_centers, None, src_kmeans, None)
            # optimize source classifier
            loss.backward()
            opt_tgt.step()

    # Evaluation

# video1_test_loader = get_coxs2v_testset(exp_dict['still_dir'],
#                                  exp_dict['video1_dir'],
#                                  exp_dict['video1_pairs'],
#                                  test_folds,
#                                  exp_dict["cross_validation_num_fold"],
#                                  data_transform,
#                                  50)
#
# video2_test_loader = get_coxs2v_testset(exp_dict['still_dir'],
#                                         exp_dict['video2_dir'],
#                                         exp_dict['video2_pairs'],
#                                         test_folds,
#                                         exp_dict["cross_validation_num_fold"],
#                                         data_transform,
#                                         50)
#
# src_acc = Evaluate(video1_test_loader,
#                    src_model,
#                    device,
#                    0,
#                    nrof_folds=10)