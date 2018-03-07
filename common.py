from os import path, makedirs
from time import time
import argparse
import pickle

import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.contrib.learn import learn_runner
from sklearn.utils import shuffle

DATA_DIRECTORY = 'data'
MAX_DOCUMENT_LENGTH = 10
MAX_VOCABULARY_SIZE = 1000000
EMBEDDING_SIZE = 15
TF_SEED = 4242
NP_SEED = 1234
CHECKPOINTS_PER_EPOCH = 5
WORD_METADATA_FILENAME = 'word_metadata.tsv'
SENTENCE_METADATA_FILENAME = 'sentence_metadata.tsv'
VOCAB_PROCESSOR_FILENAME = 'vocab_processor.pickle'
DATA_FILENAME = 'data.pickle'
VERBOSITY = 'info'
WORDS_FEATURE = 'words'  # Name of the input words feature.


"""
Timing functions (MATLAB style)
"""


_tstart_stack = []


def tic():
    _tstart_stack.append(time())


def toc(fmt="Elapsed: %.2f s"):
    print(fmt % (time() - _tstart_stack.pop()))


"""
Command line argument handling
"""


def create_parser(model_dir=None):
    """Creates a parser object with arguments common to all executables in the project."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--data-dir',
        default=DATA_DIRECTORY,
        help='Data directory (default: {})'.format(DATA_DIRECTORY))
    parser.add_argument(
        '--max-doc-len',
        type=int,
        default=MAX_DOCUMENT_LENGTH,
        help='Discard any words in a document beyond this number (default: {})'.format(MAX_DOCUMENT_LENGTH))
    parser.add_argument(
        '--vocab-processor-file',
        default=VOCAB_PROCESSOR_FILENAME,
        help='Base filename of the vocabulary processor (default: {})'.format(VOCAB_PROCESSOR_FILENAME))
    parser.add_argument(
        '--max-vocab-size',
        type=int,
        default=MAX_VOCABULARY_SIZE,
        help='Discard any new vocabulary beyond this (default: {})'.format(MAX_VOCABULARY_SIZE))

    return parser


def create_parser_training(model_dir=None, n_epochs=None, batch_size=None, learning_rate=None):
    """Creates a parser form executables that train models."""
    parser = create_parser(model_dir)

    parser.add_argument(
        '--model-dir',
        default=model_dir,
        help='Model directory (default: {})'.format(model_dir))
    parser.add_argument(
        '--n-epochs',
        type=int,
        default=n_epochs,
        help='Number of training epochs (default: {})'.format(n_epochs))
    parser.add_argument(
        '--batch-size',
        default=batch_size,
        help='Training batch size (default: {}). Use "None" for full batch training.'.format(batch_size))
    parser.add_argument(
        '--learning-rate',
        type=float,
        default=learning_rate,
        help='Learning rate (default: {})'.format(learning_rate))
    parser.add_argument(
        '--checkpoints-per-epoch',
        type=int,
        default=CHECKPOINTS_PER_EPOCH,
        help='Number of checkpoints per training epoch (default: {})'.format(CHECKPOINTS_PER_EPOCH))
    parser.add_argument(
        '--verbosity',
        default=VERBOSITY,
        help='Tensorflow verbosity: debug, info, warning or error (default: {})'.format(VERBOSITY))
    parser.add_argument(
        '--tf-seed',
        type=int,
        default=TF_SEED,
        help='Random seed for Tensorflow.')
    parser.add_argument(
        '--np-seed',
        type=int,
        default=NP_SEED,
        help='Random Seed for Numpy. Used for shuffling and splitting data.')

    return parser


def parse_arguments(parser):
    """Extracts flags from the parser object."""
    flags = parser.parse_args()

    verbosity = flags.verbosity
    if verbosity == 'debug':
        flags.verbosity = tf.logging.DEBUG
    elif verbosity == 'info':
        flags.verbosity = tf.logging.INFO
    elif verbosity == 'warning':
        flags.verbosity = tf.logging.WARN
    elif verbosity == 'error':
        flags.verbosity = tf.logging.ERROR
    else:
        raise ValueError('Invalid verbosity argument.')

    if flags.batch_size == 'None':
        flags.batch_size = None
    else:
        flags.batch_size = int(flags.batch_size)

    return flags


"""
Data processing
"""


def get_data(data_directory, classes_only=False):
    """Load data from the data_directory."""
    classes = pd.read_csv(path.join(data_directory, 'classes.txt'), header=None, names=['class'])
    if classes_only:
        return classes
    train_raw = pd.read_csv(path.join(data_directory, 'train.csv'), header=None)
    test_raw = pd.read_csv(path.join(data_directory, 'test.csv'), header=None)
    longest_sent = max([len(sent) for sent in tf.contrib.learn.preprocessing.tokenizer(train_raw[2])])
    print("The longest sentence in the training data has {} words.".format(longest_sent))

    return train_raw, test_raw, classes


def extract_data(train_raw, test_raw):
    """Extract the document and class from each entry in the data."""
    x_train = train_raw[2]
    y_train = train_raw[0]
    x_test = test_raw[2]
    y_test = test_raw[0]
    print('Size of training set: {0}'.format(len(x_train)))
    print('Size of test set: {0}'.format(len(x_test)))
    return x_train, np.array(y_train), x_test, np.array(y_test)


def process_vocabulary(train_sentences, test_sentences, flags, reuse=True, vocabulary_processor=None, extend=False):
    """Map words to integers, and then map sentences to integer sequences of length flags.max_doc_len, by truncating and
       padding as needed. This leads to an integer matrix of data which is what TensorFlow can work with. The processor
       is then saved to disk in a file determined by flags.

    Args:
       reuse: if True load the vocabulary_processor is loaded from disk if the file exists.
       vocabulary_processor: if not None, and it was not loaded from disk, the passed vocabulary_processor is used.
       extend: if True the vocabulary processor (loaded or passed) is extended.
    """

    vocabulary_processor_path = path.join(flags.model_dir, flags.vocab_processor_file)
    # If vocabulary_processor gets created/altered save it.
    if reuse and path.isfile(vocabulary_processor_path):
        vocabulary_processor = tf.contrib.learn.preprocessing.VocabularyProcessor.restore(vocabulary_processor_path)
        save_vocab_processor = extend
    elif vocabulary_processor is None:
        vocabulary_processor = tf.contrib.learn.preprocessing.VocabularyProcessor(flags.max_doc_len)
        vocabulary_processor.fit(train_sentences)
        save_vocab_processor = True
    elif extend:
        vocabulary_processor.vocabulary_.freeze(False)
        vocabulary_processor.fit(train_sentences)
        save_vocab_processor = True
    else:
        save_vocab_processor = False

    if train_sentences is not None:
        train_bow = np.array(list(vocabulary_processor.transform(train_sentences)))
    else:
        train_bow = None
    if test_sentences is not None:
        test_bow = np.array(list(vocabulary_processor.transform(test_sentences)))
    else:
        test_bow = None
    n_words = len(vocabulary_processor.vocabulary_)
    print('Number of words in vocabulary: %d' % n_words)

    if save_vocab_processor:
        if not path.isdir(flags.model_dir):
            makedirs(flags.model_dir)
        vocabulary_processor.save(vocabulary_processor_path)

    return train_bow, test_bow, vocabulary_processor, n_words


def preprocess_data(flags):
    """Load data, shuffle it, process the vocabulary and save to DATA_FILENAME, if not done already. Returns saved data.
       NOTE: If the max_doc_len changes from a previous run, then DATA_FILENAME should be deleted so that it can be
       properly recreated."""

    data_path = path.join(flags.model_dir, DATA_FILENAME)
    if path.isfile(data_path):
        with open(data_path, 'rb') as f:
            train_raw, x_train, y_train, x_test, y_test, classes = pickle.load(f)
    else:
        train_raw, test_raw, classes = get_data(flags.data_dir)

        # Seeding is necessary for reproducability.
        np.random.seed(flags.np_seed)
        # Shuffle data to make the distribution of classes roughly stratified for each mini-batch.
        # This is not necessary for full batch training, but is essential for mini-batch training.
        train_raw = shuffle(train_raw)
        test_raw = shuffle(test_raw)
        train_sentences, y_train, test_sentences, y_test = extract_data(train_raw, test_raw)
        x_train, x_test, vocabulary_processor, n_words = process_vocabulary(train_sentences, test_sentences, flags)
        with open(data_path, 'wb') as f:
            pickle.dump([train_raw, x_train, y_train, x_test, y_test, classes], f)

    return train_raw, x_train, y_train, x_test, y_test, classes


"""
Modelling: Training, evaluation and prediction. Also metadata for TensorBoard.
"""


def input_fn(x, y=None, batch_size=None, num_epochs=None, shuffle=False):
    """Generic input function to be used as then input_fn arguments for Experiment or directly with Estimators."""
    if batch_size is None and x is not None:
        batch_size = len(x)
    return tf.estimator.inputs.numpy_input_fn(
        {WORDS_FEATURE: x},
        y,
        batch_size=batch_size,
        num_epochs=num_epochs,
        shuffle=shuffle)


def run_experiment(x_train, y_train, x_dev, y_dev, model_fn, schedule, flags):
    """Create experiment object and run it."""
    hparams = tf.contrib.training.HParams(
        n_words=flags.max_vocab_size,
        n_epochs=flags.n_epochs,
        seed=flags.tf_seed,
        batch_size=flags.batch_size,
        learning_rate=flags.learning_rate,
        output_dim=flags.output_dim
    )
    if hasattr(flags, 'embed_dim'):
        hparams.embed_dim = flags.embed_dim
    is_training = schedule in ['train', 'train_and_evaluate']

    run_config = tf.contrib.learn.RunConfig()
    try:
        checkpoint_steps = len(x_train) / flags.checkpoints_per_epoch / flags.batch_size if is_training else None
        log_step_count_steps = 100  # default value
    except TypeError:
        # Happens if batch_size is None
        checkpoint_steps = 1
        log_step_count_steps = 1
    run_config = run_config.replace(model_dir=flags.model_dir,
                                    #save_summary_steps= 500,
                                    save_checkpoints_steps=checkpoint_steps,
                                    log_step_count_steps=log_step_count_steps,
                                    tf_random_seed=hparams.seed)

    # We want this after experiment_fn returns.
    estimator = tf.estimator.Estimator(
        model_fn=model_fn,
        config=run_config,
        params=hparams
    )

    def experiment_fn(run_config, hparams):
        experiment = tf.contrib.learn.Experiment(
            estimator=estimator,
            train_input_fn=input_fn(x_train, y_train,
                                    batch_size=hparams.batch_size, num_epochs=hparams.n_epochs, shuffle=True),
            eval_input_fn=input_fn(x_dev, y_dev,
                                   num_epochs=1),
            eval_delay_secs=0
        )
        return experiment

    if schedule in ['train', 'train_and_evaluate']:
        print('Training model for {} epochs...'.format(hparams.n_epochs))
    learn_runner.run(
        experiment_fn=experiment_fn,
        run_config=run_config,
        schedule=schedule,  # What to run, e.g. "train_and_evaluate", "evaluate", ...
        hparams=hparams  # hyperparameters
    )

    #input_example = tf.placeholder(dtype=tf.int64, shape=[None, 10])
    #serving_input_receiver_fn = tf.estimator.export.build_raw_serving_input_receiver_fn({WORDS_FEATURE: input_example})
    #estimator.export_savedmodel(flags.model_dir, serving_input_receiver_fn)


def predict(x_data, model_fn, flags):
    """Performs classification on the given x_data using the model given by model_fn."""
    hparams = tf.contrib.training.HParams(
        n_words=flags.max_vocab_size,
        output_dim=flags.output_dim,
        embed_dim=flags.embed_dim
    )

    run_config = tf.contrib.learn.RunConfig()
    run_config = run_config.replace(model_dir=flags.model_dir)
    predictions = tf.estimator.Estimator(
        model_fn=model_fn,
        config=run_config,
        params=hparams
    ).predict(input_fn(x_data, num_epochs=1))
    return [p['class'] for p in predictions]


def create_metadata(train_raw, classes, flags):
    """Create word-embedding and sentence-embedding metadata files for TensorBoard if they do not already exist."""
    word_embedding_metadata_filename = flags.word_meta_file
    if not path.isfile(word_embedding_metadata_filename):
        print("Creating word-embedding metadata for TensorBoard...")
        # Create the word-embedding metadata file. This is the vocabulary listed in the order its enumeration
        # for the embedding.
        vocabulary_processor = tf.contrib.learn.preprocessing.VocabularyProcessor.restore(flags.vocab_processor_file)

        vocab = vocabulary_processor.vocabulary_._mapping
        with open(word_embedding_metadata_filename, 'w') as f:
            for w in sorted(vocab, key=vocab.get):
                f.write('%s\n' % w)
            # Note that we left "extra room" for the vocabulary to grow.
            for i in range(len(vocab), flags.max_vocab_size):
                f.write('%s\n' % vocabulary_processor.vocabulary_._unknown_token)

    sentence_embedding_metadata_filename = flags.sent_meta_file
    if not path.isfile(sentence_embedding_metadata_filename):
        print("Creating sentence-embedding metadata for TensorBoard...")
        # Create the sentence-embedding metadata file
        with open(sentence_embedding_metadata_filename, 'w') as f:
            f.write("Label\tTitle\tDocument\n")
            for row in train_raw.itertuples():
                label = classes.iloc[row[1] - 1].item()
                # title = tf.compat.as_text(row[2]).encode('utf-8')
                title = row[2]
                # sent = tf.compat.as_text(row[3]).encode('utf-8')
                sent = row[3]
                f.write("%s\t%s\t%s\n" % (label, title, sent))


def estimator_spec_for_softmax_classification(logits, labels, mode, params):
    """Returns EstimatorSpec instance for softmax classification."""
    predicted_class = tf.argmax(logits, 1)
    if mode == tf.estimator.ModeKeys.PREDICT:
        return tf.estimator.EstimatorSpec(
            mode=mode,
            predictions={
                'class': predicted_class,
                'prob': tf.nn.softmax(logits)
            },
            export_outputs={
                'class': tf.estimator.export.PredictOutput(predicted_class)
            })

    onehot_labels = tf.one_hot(labels, params.output_dim, 1, 0)
    loss = tf.losses.softmax_cross_entropy(onehot_labels=onehot_labels, logits=logits)
    if mode == tf.estimator.ModeKeys.TRAIN:
        with tf.name_scope('OptimizeLoss'):
            optimizer = tf.train.AdamOptimizer(learning_rate=params.learning_rate)
            train_op = optimizer.minimize(loss, global_step=tf.train.get_global_step())
        return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op)

    # mode == EVAL
    eval_metric_ops = {
        'accuracy': tf.metrics.accuracy(labels=labels, predictions=predicted_class)
    }
    return tf.estimator.EstimatorSpec(mode=mode, loss=loss, eval_metric_ops=eval_metric_ops)
