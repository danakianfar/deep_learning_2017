#!/usr/bin/python
# -*- coding: utf-8 -*-

# MIT License
#
# Copyright (c) 2017 Tom Runia
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to conditions.
#
# Author: Tom Runia
# Date Created: 2017-10-19

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import time
from datetime import datetime
import argparse

import numpy as np
import pickle
import tensorflow as tf

from dataset import TextDataset
from model import TextGenerationModel


def init_summary_writer(sess, save_path):
    # Optional to use.
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    return tf.summary.FileWriter(save_path, sess.graph)


def _ensure_path_exists(path):
    if not tf.gfile.Exists(path):
        tf.gfile.MakeDirs(path)


def train(config):
    tf.reset_default_graph()
    # Initialize the text dataset
    dataset = TextDataset(config.txt_file, config.clean_data)

    # Initialize the model
    model = TextGenerationModel(
        batch_size=config.batch_size,
        seq_length=config.seq_length,
        vocabulary_size=dataset.vocab_size,
        lstm_num_hidden=config.lstm_num_hidden,
        lstm_num_layers=config.lstm_num_layers,
        embed_dim=config.embed_dim,
        decoding_model=config.decoding_mode
    )

    ###########################################################################
    # Implement code here.
    ###########################################################################

    # Reproducibility
    tf.set_random_seed(42)
    np.random.seed(42)

    # Utility vars and ops
    gpu_opts = tf.GPUOptions(per_process_gpu_memory_fraction=config.gpu_mem_frac, allow_growth=True)
    session = tf.Session(config=tf.ConfigProto(gpu_options=gpu_opts))
    global_step = tf.Variable(0, trainable=False, name='global_step')

    # logging
    train_logdir = os.path.join(config.summary_path, '{}_train'.format(config.model_name))
    train_log_writer = init_summary_writer(session, train_logdir)

    # Define the optimizer
    if config.optimizer.lower() == 'rmsprop':
        optimizer = tf.train.RMSPropOptimizer(learning_rate=config.learning_rate, decay=config.learning_rate_decay)
    elif config.optimizer.lower() == 'adam':
        optimizer = tf.train.AdamOptimizer(config.learning_rate)

    # Compute the gradients for each variable
    grads_and_vars = optimizer.compute_gradients(model.loss)
    train_op = optimizer.apply_gradients(grads_and_vars, global_step)
    grads, variables = zip(*grads_and_vars)
    grads_clipped, _ = tf.clip_by_global_norm(grads, clip_norm=config.max_norm_gradient)
    apply_gradients_op = optimizer.apply_gradients(zip(grads_clipped, variables), global_step=global_step)
    saver = tf.train.Saver(max_to_keep=50)
    save_path = os.path.join(config.checkpoint_path, '{}/model.ckpt'.format(config.model_name))
    _ensure_path_exists(save_path)

    # Summaries
    summary_op = tf.summary.merge_all()
    session.run(fetches=[tf.global_variables_initializer(), tf.local_variables_initializer()])
    decoded_seqs = {}

    for train_step in range(int(config.train_steps)):

        # dim: [batch_size, time_step]
        batch_inputs, batch_labels = dataset.batch(batch_size=config.batch_size, seq_length=config.seq_length)

        # Time-major: [time_step, batch_size]
        batch_inputs = batch_inputs.T

        # Only for time measurement of step through network
        t1 = time.time()

        #######################################################################
        # Implement code here
        #######################################################################
        train_feed = {model.inputs: batch_inputs,
                      model.labels: batch_labels,
                      model.initial_lstm_states: model.zero_state_numpy()}
        fetches = [model.loss, apply_gradients_op]
        if train_step % config.print_every == 0:
            fetches += [summary_op]
            loss, _, summary = session.run(feed_dict=train_feed, fetches=fetches)
            train_log_writer.add_summary(summary, train_step)
        else:
            loss, _ = session.run(feed_dict=train_feed, fetches=fetches)

        # Only for time measurement of step through network
        t2 = time.time()
        examples_per_second = config.batch_size / float(t2 - t1)

        # Output the training progress
        if train_step % config.print_every == 0:
            print("[{}] Train Step {:04d}/{:04d}, Batch Size = {}, Examples/Sec = {:.2f}, Loss = {}".format(
                datetime.now().strftime("%Y-%m-%d %H:%M"), train_step + 1,
                int(config.train_steps), config.batch_size, examples_per_second, loss))

        # Decode
        if train_step % config.sample_every == 0:
            t3 = time.time()
            decode_feed = {model.decode_length: config.decode_length,
                           model.initial_lstm_states: model.zero_state_numpy(),
                           model.random_initial_decoding_inputs: np.random.choice(a=dataset.vocab_size,
                                                                                  size=(config.batch_size))}
            fetches = [model.decoded_sequence]
            decoded_tokens = session.run(fetches=fetches, feed_dict=decode_feed)
            decoded_seqs[train_step] = np.array(decoded_tokens).squeeze()

            print('Decoded at train step {}, Sequences/Sec {:.2f}'.format(str(train_step),
                                                                          config.batch_size / float(time.time() - t3)))

            # print("".join([dataset._ix_to_char[x] for x in decoded_seqs[train_step][:, 0]]))

        if train_step % config.checkpoint_every == 0:
            saver.save(session, save_path=save_path)

    train_log_writer.close()
    with open('{}_decoded_seqs.pkl'.format(config.model_name), 'wb') as f:
        pickle.dump(decoded_seqs, f)


if __name__ == "__main__":
    # Parse training configuration
    parser = argparse.ArgumentParser()

    # Model params
    parser.add_argument('--txt_file', type=str, default='./books/book_EN_carl_sagan_compilation.txt',
                        help="Path to a .txt file to train on")
    parser.add_argument('--seq_length', type=int, default=30, help='Length of an input sequence')
    parser.add_argument('--lstm_num_hidden', type=int, default=128, help='Number of hidden units in the LSTM')
    parser.add_argument('--lstm_num_layers', type=int, default=2, help='Number of LSTM layers in the model')

    # Training params
    parser.add_argument('--batch_size', type=int, default=128, help='Number of examples to process in a batch')
    parser.add_argument('--learning_rate', type=float, default=2e-3, help='Learning rate')
    parser.add_argument('--learning_rate_decay', type=float, default=0.96, help='Learning rate decay fraction')
    parser.add_argument('--learning_rate_step', type=int, default=5000, help='Learning rate step')
    parser.add_argument('--embed_dim', type=int, default=40,
                        help='Embedding dimension. Integer, default is 40')
    parser.add_argument('--dropout_keep_prob', type=float, default=1.0, help='Dropout keep probability')
    parser.add_argument('--train_steps', type=int, default=2e4, help='Number of training steps')
    parser.add_argument('--max_norm_gradient', type=float, default=5.0, help='--')
    parser.add_argument('--optimizer', type=str, choices=['adam', 'rmsprop'], default="RMSProp",
                        help='Optimizer, choose between adam and rmsprop')
    parser.add_argument('--clean_data', type=bool, default=True,
                        help='Whether to remove unnecessary characters from the dataset')

    # Misc params
    parser.add_argument('--gpu_mem_frac', type=float, default=0.5, help='Fraction of GPU memory to allocate')
    parser.add_argument('--log_device_placement', type=bool, default=False, help='Log device placement for debugging')
    parser.add_argument('--summary_path', type=str, default="./summaries/", help='Output path for summaries')
    parser.add_argument('--print_every', type=int, default=10, help='How often to print training progress')
    parser.add_argument('--sample_every', type=int, default=200, help='How often to sample from the model')
    parser.add_argument('--checkpoint_every', type=int, default=500, help='How often to save the model')
    parser.add_argument('--checkpoint_path', type=str, default='./checkpoints/', help='Checkpoint directory')

    parser.add_argument('--decoding_mode', type=str, choices=['greedy', 'sampling'], default='sampling',
                        help='Decode by greedy or sampling.')
    parser.add_argument('--decode_length', type=int, default=100,
                        help='Inference (decoding) number of steps, int default is 30')
    parser.add_argument('--model_name', type=str, default='lstm_carl_sagan', help='Model name for saving')
    parser.add_argument('--grid_search', type=bool, default=False, help='Grid search')
    config = parser.parse_args()

    if config.grid_search:

        for decoding_mode in ['sampling']:
            for learning_rate in [2e-3]:
                for txt_file in ['./books/holy_koran.txt',
                                 './books/origin_of_species.txt',
                                 #'./books/carl_sagan.txt'
                                 ]:

                    model_name = '{}_({}_{})_{}'.format(txt_file.replace('./books/', ''), optimizer, learning_rate,
                                                        decoding_mode)
                    config.decoding_mode = decoding_mode
                    config.learning_rate = learning_rate
                    config.optimizer = optimizer
                    config.txt_file = txt_file
                    config.model_name = model_name

                    print('Grid Search \n {}'.format(str(config)))
                    train(config)
                for optimizer in ['rmsprop']:
                    pass
    else:
        # Train the model
        train(config)
