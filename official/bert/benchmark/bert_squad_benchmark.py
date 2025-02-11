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
"""Executes BERT SQuAD benchmarks and accuracy tests."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import json
import os
import time

import tensorflow as tf
# pylint: disable=g-bad-import-order
from absl import flags
from absl.testing import flagsaver
from official.bert import run_squad
from official.bert.benchmark import benchmark_utils
from official.bert.benchmark import squad_evaluate_v1_1
from official.utils.misc import distribution_utils

# pylint: enable=g-bad-import-order

# pylint: disable=line-too-long
PRETRAINED_CHECKPOINT_PATH = 'gs://cloud-tpu-checkpoints/bert/tf_20/uncased_L-24_H-1024_A-16/bert_model.ckpt'
SQUAD_TRAIN_DATA_PATH = 'gs://tf-perfzero-data/bert/squad/squad_train.tf_record'
SQUAD_PREDICT_FILE = 'gs://tf-perfzero-data/bert/squad/dev-v1.1.json'
SQUAD_VOCAB_FILE = 'gs://tf-perfzero-data/bert/squad/vocab.txt'
SQUAD_SMALL_INPUT_META_DATA_PATH = 'gs://tf-perfzero-data/bert/squad/squad_small_meta_data'
SQUAD_FULL_INPUT_META_DATA_PATH = 'gs://tf-perfzero-data/bert/squad/squad_full_meta_data'
MODEL_CONFIG_FILE_PATH = 'gs://cloud-tpu-checkpoints/bert/tf_20/uncased_L-24_H-1024_A-16/bert_config'
# pylint: enable=line-too-long

FLAGS = flags.FLAGS


class BertSquadBenchmarkBase(benchmark_utils.BertBenchmarkBase):
  """Base class to hold methods common to test classes in the module."""

  def _read_training_summary_from_file(self):
    """Reads the training summary from a file."""
    summary_path = os.path.join(FLAGS.model_dir, 'training_summary.txt')
    with tf.io.gfile.GFile(summary_path, 'rb') as reader:
      return json.loads(reader.read().decode('utf-8'))

  def _read_input_meta_data_from_file(self):
    """Reads the input metadata from a file."""
    with tf.io.gfile.GFile(FLAGS.input_meta_data_path, 'rb') as reader:
      return json.loads(reader.read().decode('utf-8'))

  def _read_predictions_dataset_from_file(self):
    """Reads the predictions dataset from a file."""
    with tf.io.gfile.GFile(SQUAD_PREDICT_FILE, 'r') as reader:
      dataset_json = json.load(reader)
      return dataset_json['data']

  def _read_predictions_from_file(self):
    """Reads the predictions from a file."""
    predictions_file = os.path.join(FLAGS.model_dir, 'predictions.json')
    with tf.io.gfile.GFile(predictions_file, 'r') as reader:
      return json.load(reader)

  def _get_distribution_strategy(self):
    """Gets the distribution strategy."""
    return distribution_utils.get_distribution_strategy(
        distribution_strategy='mirrored', num_gpus=self.num_gpus)

  @flagsaver.flagsaver
  def _train_squad(self):
    """Runs BERT SQuAD training."""
    input_meta_data = self._read_input_meta_data_from_file()
    strategy = self._get_distribution_strategy()

    run_squad.train_squad(
        strategy=strategy,
        input_meta_data=input_meta_data,
        custom_callbacks=[self.timer_callback])

  @flagsaver.flagsaver
  def _evaluate_squad(self):
    """Runs BERT SQuAD evaluation."""
    input_meta_data = self._read_input_meta_data_from_file()
    strategy = self._get_distribution_strategy()

    run_squad.predict_squad(strategy=strategy, input_meta_data=input_meta_data)

    dataset = self._read_predictions_dataset_from_file()
    predictions = self._read_predictions_from_file()

    eval_metrics = squad_evaluate_v1_1.evaluate(dataset, predictions)
    # Use F1 score as reported evaluation metric.
    self.eval_metrics = eval_metrics['f1']


class BertSquadBenchmarkReal(BertSquadBenchmarkBase):
  """Short benchmark performance tests for BERT SQuAD model.

  Tests BERT SQuAD performance in different GPU configurations.
  The naming convention of below test cases follow
  `benchmark_(number of gpus)_gpu` format.
  """

  def __init__(self, output_dir=None, **kwargs):
    super(BertSquadBenchmarkReal, self).__init__(output_dir=output_dir)

  def _setup(self):
    """Sets up the benchmark and SQuAD flags."""
    super(BertSquadBenchmarkReal, self)._setup()
    FLAGS.train_data_path = SQUAD_TRAIN_DATA_PATH
    FLAGS.predict_file = SQUAD_PREDICT_FILE
    FLAGS.vocab_file = SQUAD_VOCAB_FILE
    FLAGS.input_meta_data_path = SQUAD_SMALL_INPUT_META_DATA_PATH
    FLAGS.bert_config_file = MODEL_CONFIG_FILE_PATH
    FLAGS.num_train_epochs = 1

  def _run_and_report_benchmark(self):
    """Runs the benchmark and reports various metrics."""
    start_time_sec = time.time()
    self._train_squad()
    wall_time_sec = time.time() - start_time_sec

    summary = self._read_training_summary_from_file()

    super(BertSquadBenchmarkReal, self)._report_benchmark(
        stats=summary,
        wall_time_sec=wall_time_sec,
        min_accuracy=0,
        max_accuracy=1)

  def benchmark_1_gpu(self):
    """Tests BERT SQuAD model performance with 1 GPU."""

    self._setup()
    self.num_gpus = 1
    FLAGS.model_dir = self._get_model_dir('benchmark_1_gpu_squad')
    FLAGS.train_batch_size = 4

    self._run_and_report_benchmark()

  def benchmark_2_gpu(self):
    """Tests BERT SQuAD model performance with 2 GPUs."""

    self._setup()
    self.num_gpus = 2
    FLAGS.model_dir = self._get_model_dir('benchmark_2_gpu_squad')
    FLAGS.train_batch_size = 8

    self._run_and_report_benchmark()

  def benchmark_4_gpu(self):
    """Tests BERT SQuAD model performance with 4 GPUs."""

    self._setup()
    self.num_gpus = 4
    FLAGS.model_dir = self._get_model_dir('benchmark_4_gpu_squad')
    FLAGS.train_batch_size = 16

    self._run_and_report_benchmark()

  def benchmark_8_gpu(self):
    """Tests BERT SQuAD model performance with 8 GPUs."""

    self._setup()
    self.num_gpus = 8
    FLAGS.model_dir = self._get_model_dir('benchmark_8_gpu_squad')
    FLAGS.train_batch_size = 32

    self._run_and_report_benchmark()


class BertSquadAccuracy(BertSquadBenchmarkBase):
  """Short accuracy test for BERT SQuAD model.

  Tests BERT SQuAD accuracy. The naming convention of below test cases follow
  `benchmark_(number of gpus)_gpu` format.
  """

  def __init__(self, output_dir=None, **kwargs):
    super(BertSquadAccuracy, self).__init__(output_dir=output_dir)

  def _setup(self):
    """Sets up the benchmark and SQuAD flags."""
    super(BertSquadAccuracy, self)._setup()
    FLAGS.train_data_path = SQUAD_TRAIN_DATA_PATH
    FLAGS.predict_file = SQUAD_PREDICT_FILE
    FLAGS.vocab_file = SQUAD_VOCAB_FILE
    FLAGS.input_meta_data_path = SQUAD_FULL_INPUT_META_DATA_PATH
    FLAGS.bert_config_file = MODEL_CONFIG_FILE_PATH
    FLAGS.init_checkpoint = PRETRAINED_CHECKPOINT_PATH
    FLAGS.num_train_epochs = 2

  def _run_and_report_benchmark(self):
    """Runs the benchmark and reports various metrics."""
    start_time_sec = time.time()
    self._train_squad()
    self._evaluate_squad()
    wall_time_sec = time.time() - start_time_sec

    summary = self._read_training_summary_from_file()
    summary['eval_metrics'] = self.eval_metrics

    super(BertSquadAccuracy, self)._report_benchmark(
        stats=summary,
        wall_time_sec=wall_time_sec,
        min_accuracy=0.900,
        max_accuracy=0.908)

  def benchmark_8_gpu(self):
    """Tests BERT SQuAD model accuracy with 8 GPUs."""

    self._setup()
    self.num_gpus = 8
    FLAGS.model_dir = self._get_model_dir('benchmark_8_gpu_squad')
    FLAGS.train_batch_size = 32

    self._run_and_report_benchmark()


if __name__ == '__main__':
  tf.test.main()
