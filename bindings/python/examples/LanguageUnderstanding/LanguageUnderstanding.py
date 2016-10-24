﻿# ==============================================================================
# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root
# for full license information.
# ==============================================================================

import os
import math
from cntk.blocks import *  # non-layer like building blocks such as LSTM()
from cntk.layers import *  # layer-like stuff such as Linear()
from cntk.models import *  # higher abstraction level, e.g. entire standard models and also operators like Sequential()
from cntk.utils import *
from cntk.io import MinibatchSource, CTFDeserializer, StreamDef, StreamDefs
from cntk import Trainer
from cntk.learner import adam_sgd, learning_rate_schedule, momentum_schedule
from cntk.ops import cross_entropy_with_softmax, classification_error

########################
# variables and stuff  #
########################

cntk_dir = os.path.dirname(os.path.abspath(__file__)) + "/../../../.."  # data resides in the CNTK folder
data_dir = cntk_dir + "/Examples/Tutorials/SLUHandsOn"                  # under Examples/Tutorials
vocab_size = 943 ; num_labels = 129 ; num_intents = 26    # number of words in vocab, slot labels, and intent labels

model_dir = "./Models"

# model dimensions
input_dim  = vocab_size
label_dim  = num_labels
emb_dim    = 150
hidden_dim = 300

########################
# define the reader    #
########################

def create_reader(path, is_training):
    return MinibatchSource(CTFDeserializer(path, StreamDefs(
        query         = StreamDef(field='S0', shape=input_dim,   is_sparse=True),
        intent_unused = StreamDef(field='S1', shape=num_intents, is_sparse=True),  # BUGBUG: unused, and should infer dim
        slot_labels   = StreamDef(field='S2', shape=label_dim,   is_sparse=True)
    )), randomize=is_training, epoch_size = None if is_training else None)   # BUGBUG: 0) gives "RuntimeError: Reading a DictionaryValue as the wrong type; Reading as type unsigned __int64 when actual type is Int"

########################
# define the model     #
########################

def create_model():
  with default_options(initial_state=0.1):  # inject an option to mimic the BS version identically; remove some day
    return Sequential([
        Embedding(emb_dim),
        Recurrence(LSTM(hidden_dim), go_backwards=False),
        Dense(label_dim)
    ])

########################
# train action         #
########################

def train(reader, model, max_epochs):
    # Input variables denoting the features and label data
    query       = Input(input_dim,  is_sparse=False)
    slot_labels = Input(num_labels, is_sparse=True)  # TODO: make sparse once it works

    # apply model to input
    z = model(query)

    # loss and metric
    ce = cross_entropy_with_softmax(z, slot_labels)
    pe = classification_error      (z, slot_labels)

    # training config
    epoch_size = 36000  #                  if max_epochs > 1 else 1000   # for fast testing
    minibatch_size = 70
    num_mbs_to_show_result = 100
    momentum_as_time_constant = minibatch_size / -math.log(0.9)  # TODO: Change to round number. This is 664.39. 700?

    lr_per_sample = [0.003]*2+[0.0015]*12+[0.0003] # LR schedule over epochs (we don't run that mayn epochs, but if we did, these are good values)

    # trainer object
    lr_schedule = learning_rate_schedule(lr_per_sample, units=epoch_size)
    learner = adam_sgd(z.parameters,
                       lr_per_sample=lr_schedule, momentum_time_constant=momentum_as_time_constant,
                       low_memory=True,
                       gradient_clipping_threshold_per_sample=15, gradient_clipping_with_truncation=True)

    trainer = Trainer(z, ce, pe, [learner])

    # define mapping from reader streams to network inputs
    input_map = {
        query       : reader.streams.query,
        slot_labels : reader.streams.slot_labels
    }

    # process minibatches and perform model training
    log_number_of_parameters(z) ; print()
    progress_printer = ProgressPrinter(freq=100, first=10, tag='Training') # more detailed logging
    #progress_printer = ProgressPrinter(tag='Training')

    t = 0
    for epoch in range(max_epochs):         # loop over epochs
        epoch_end = (epoch+1) * epoch_size
        while t < epoch_end:               # loop over minibatches on the epoch
            # BUGBUG? The change of minibatch_size parameter vv has no effect.
            data = reader.next_minibatch(min(minibatch_size, epoch_end-t), input_map=input_map) # fetch minibatch
            trainer.train_minibatch(data)                                   # update model with it
            t += data[slot_labels].num_samples                              # count samples processed so far
            progress_printer.update_with_trainer(trainer, with_metric=True) # log progress
            #def trace_node(name):
            #    nl = [n for n in z.parameters if n.name() == name]
            #    if len(nl) > 0:
            #        print (name, np.asarray(nl[0].value))
            #trace_node('W')
            #trace_node('stabilizer_param')
        loss, metric, actual_samples = progress_printer.epoch_summary(with_metric=True)

    return loss, metric # return values from last epoch

########################
# eval action          #
########################

def evaluate(reader, model):
    # Input variables denoting the features and label data
    query       = Input(input_dim,  is_sparse=False)
    slot_labels = Input(num_labels, is_sparse=True)  # TODO: make sparse once it works

    # apply model to input
    z = model(query)

    # loss and metric
    ce = cross_entropy_with_softmax(z, slot_labels)
    pe = classification_error      (z, slot_labels)

    # define mapping from reader streams to network inputs
    input_map = {
        query       : reader.streams.query,
        slot_labels : reader.streams.slot_labels
    }

    # process minibatches and perform evaluation
    dummy_learner = adam_sgd(z.parameters, lr_per_sample=1, momentum_time_constant=0, low_memory=True) # BUGBUG: should not be needed
    evaluator = Trainer(z, ce, pe, [dummy_learner])
    progress_printer = ProgressPrinter(freq=100, first=10, tag='Evaluation') # more detailed logging
    #progress_printer = ProgressPrinter(tag='Evaluation')

    while True:
        # BUGBUG? The change of minibatch_size parameter vv has no effect.
        minibatch_size = 1000
        data = reader.next_minibatch(minibatch_size, input_map=input_map) # fetch minibatch
        if not data:                                                      # until we hit the end
            break
        metric = evaluator.test_minibatch(data)                           # evaluate minibatch
        _loss = 0 # evaluator.previous_minibatch_loss_average  --BUGBUG: crashes Python
        _ns = progress_printer.update(_loss, data[slot_labels].num_samples, metric) # log progress
        if _ns >= 10984:  # BUGBUG: currently the only way it seems
            break
        # BUGBUG: progress printer does not know that there is no loss reported for testing
    loss, metric, actual_samples = progress_printer.epoch_summary(with_metric=True)

    return loss, metric

#############################
# main function boilerplate #
#############################

if __name__=='__main__':
    # TODO: leave these in for now as debugging aids; remove for beta
    from _cntk_py import set_computation_network_trace_level, set_fixed_random_seed, force_deterministic_algorithms
    #set_computation_network_trace_level(1)  # TODO: remove debugging facilities once this all works
    set_fixed_random_seed(1)  # BUGBUG: has no effect at present  # TODO: remove debugging facilities once this all works
    force_deterministic_algorithms()

    # create the model
    model = create_model()

    # train
    reader = create_reader(data_dir + "/atis.train.ctf", is_training=True)
    train(reader, model, max_epochs=1)

    # test
    reader = create_reader(data_dir + "/atis.test.ctf", is_training=False)
    evaluate(reader, model)