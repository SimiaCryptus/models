# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Run masked LM/next sentence masked_lm pre-training for BERT in tf2.0."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import functools

import tensorflow as tf
from absl import app
from absl import flags
from absl import logging
# Import BERT model libraries.
from official.bert import bert_models
from official.bert import input_pipeline
from official.bert import model_training_utils
from official.bert import modeling
from official.bert import optimization
from official.bert import tpu_lib

flags.DEFINE_string('input_files', None,
                    'File path to retrieve training data for pre-training.')
flags.DEFINE_string('bert_config_file', None,
                    'Bert configuration file to define core bert layers.')
flags.DEFINE_string(
    'model_dir', None,
    ('The directory where the model weights and training/evaluation summaries '
     'are stored. If not specified, save to /tmp/bert20/.'))
flags.DEFINE_string('tpu', '', 'TPU address to connect to.')
flags.DEFINE_enum(
    'strategy_type', 'mirror', ['tpu', 'mirror'],
    'Distribution Strategy type to use for training. `tpu` uses '
    'TPUStrategy for running on TPUs, `mirror` uses GPUs with '
    'single host.')
# Model training specific flags.
flags.DEFINE_integer(
    'max_seq_length', 128,
    'The maximum total input sequence length after WordPiece tokenization. '
    'Sequences longer than this will be truncated, and sequences shorter '
    'than this will be padded.')
flags.DEFINE_integer('max_predictions_per_seq', 20,
                     'Maximum predictions per sequence_output.')
flags.DEFINE_integer('train_batch_size', 32, 'Total batch size for training.')
flags.DEFINE_integer('num_train_epochs', 3,
                     'Total number of training epochs to perform.')
flags.DEFINE_integer('num_steps_per_epoch', 1000,
                     'Total number of training steps to run per epoch.')
flags.DEFINE_float('learning_rate', 5e-5, 'The initial learning rate for Adam.')
flags.DEFINE_float('warmup_steps', 10000,
                   'Warmup steps for Adam weight decay optimizer.')

FLAGS = flags.FLAGS


def get_pretrain_input_data(input_file_pattern, seq_length,
                            max_predictions_per_seq, batch_size, strategy):
  """Returns input dataset from input file string."""

  # When using TPU pods, we need to clone dataset across
  # workers and need to pass in function that returns the dataset rather
  # than passing dataset instance itself.
  use_dataset_fn = isinstance(strategy, tf.distribute.experimental.TPUStrategy)
  if use_dataset_fn:
    if batch_size % strategy.num_replicas_in_sync != 0:
      raise ValueError(
          'Batch size must be divisible by number of replicas : {}'.format(
              strategy.num_replicas_in_sync))

    # As auto rebatching is not supported in
    # `experimental_distribute_datasets_from_function()` API, which is
    # required when cloning dataset to multiple workers in eager mode,
    # we use per-replica batch size.
    batch_size = int(batch_size / strategy.num_replicas_in_sync)

  def _dataset_fn(ctx=None):
    del ctx

    input_files = []
    for input_pattern in input_file_pattern.split(','):
      input_files.extend(tf.io.gfile.glob(input_pattern))

    train_dataset = input_pipeline.create_pretrain_dataset(
        input_files, seq_length, max_predictions_per_seq, batch_size)
    return train_dataset

  return _dataset_fn if use_dataset_fn else _dataset_fn()


def get_loss_fn(loss_scale=1.0):
  """Returns loss function for BERT pretraining."""

  def _bert_pretrain_loss_fn(unused_labels, losses, **unused_args):
    return tf.keras.backend.mean(losses) * loss_scale

  return _bert_pretrain_loss_fn


def run_customized_training(strategy,
                            bert_config,
                            max_seq_length,
                            max_predictions_per_seq,
                            model_dir,
                            steps_per_epoch,
                            epochs,
                            initial_lr,
                            warmup_steps,
                            input_files,
                            train_batch_size,
                            use_remote_tpu=False):
  """Run BERT pretrain model training using low-level API."""

  train_input_fn = functools.partial(get_pretrain_input_data, input_files,
                                     max_seq_length, max_predictions_per_seq,
                                     train_batch_size, strategy)

  def _get_pretrain_model():
    pretrain_model, core_model = bert_models.pretrain_model(
        bert_config, max_seq_length, max_predictions_per_seq)
    pretrain_model.optimizer = optimization.create_optimizer(
        initial_lr, steps_per_epoch * epochs, warmup_steps)
    return pretrain_model, core_model

  return model_training_utils.run_customized_training_loop(
      strategy=strategy,
      model_fn=_get_pretrain_model,
      loss_fn=get_loss_fn(),
      model_dir=model_dir,
      train_input_fn=train_input_fn,
      steps_per_epoch=steps_per_epoch,
      epochs=epochs,
      use_remote_tpu=use_remote_tpu)


def run_bert_pretrain(strategy):
  """Runs BERT pre-training."""

  bert_config = modeling.BertConfig.from_json_file(FLAGS.bert_config_file)
  if not strategy:
    raise ValueError('Distribution strategy is not specified.')

  # Runs customized training loop.
  logging.info('Training using customized training loop TF 2.0 with distrubuted'
               'strategy.')

  use_remote_tpu = (FLAGS.strategy_type == 'tpu' and FLAGS.tpu)
  return run_customized_training(
      strategy,
      bert_config,
      FLAGS.max_seq_length,
      FLAGS.max_predictions_per_seq,
      FLAGS.model_dir,
      FLAGS.num_steps_per_epoch,
      FLAGS.num_train_epochs,
      FLAGS.learning_rate,
      FLAGS.warmup_steps,
      FLAGS.input_files,
      FLAGS.train_batch_size,
      use_remote_tpu=use_remote_tpu)


def main(_):
  # Users should always run this script under TF 2.x
  assert tf.version.VERSION.startswith('2.')

  if not FLAGS.model_dir:
    FLAGS.model_dir = '/tmp/bert20/'
  strategy = None
  if FLAGS.strategy_type == 'mirror':
    strategy = tf.distribute.MirroredStrategy()
  elif FLAGS.strategy_type == 'tpu':
    # Initialize TPU System.
    cluster_resolver = tpu_lib.tpu_initialize(FLAGS.tpu)
    strategy = tf.distribute.experimental.TPUStrategy(cluster_resolver)
  else:
    raise ValueError('The distribution strategy type is not supported: %s' %
                     FLAGS.strategy_type)
  if strategy:
    print('***** Number of cores used : ', strategy.num_replicas_in_sync)

  return run_bert_pretrain(strategy)


if __name__ == '__main__':
  app.run(main)
