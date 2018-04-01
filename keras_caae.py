# Conditional adversarial autoencoder
import keras
import keras.models as models
import keras.layers as layers
import keras.metrics as metrices
from keras.layers import Input, Dense, Reshape, Flatten, Dropout, multiply, GaussianNoise
from keras.layers import BatchNormalization, Activation, Embedding, ZeroPadding2D
from keras import losses
from keras.utils import to_categorical
import keras.backend as K
from data_loader import load_data
from keras.models import load_model
import matplotlib.pyplot as plt
from keras_contrib.layers.normalization import InstanceNormalization
from keras.layers import Input, Dense, Reshape, Flatten, Dropout, Concatenate
from keras.layers import BatchNormalization, Activation, ZeroPadding2D
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.convolutional import UpSampling2D, Conv2D
from keras.models import Sequential, Model
from keras.optimizers import Adam
import datetime
import sys
import tensorflow as tf
import numpy as np

from keras.preprocessing import image
from keras_vggface.vggface import VGGFace
from keras_vggface import utils

# facenet
fnet = VGGFace(include_top=False, input_shape=(128, 128, 3))


def face_recognition_loss(img, pred):
    return keras.losses.mse(img, pred) # +K.mean(K.sum(K.abs(fnet(img) - fnet(pred)), axis=1))


class CAAE:
    def __init__(self):
        self.rows = 128
        self.cols = 128
        self.channels = 3
        self.img_shape = (self.rows, self.cols, self.channels)
        self.encoded_dim = 1000
        self.num_classes = 10

        self.gf = 32
        self.df = 64

        # optimizer
        optimizer = keras.optimizers.Adam(0.0002, 0.5)

        # discriminator
        self.discriminator = self.build_discriminator()
        self.discriminator.compile(optimizer=optimizer, loss=losses.binary_crossentropy,
                                   metrics=[metrices.binary_accuracy])

        # encoder
        self.encoder = self.build_encoder()
        self.encoder.compile(loss=['binary_crossentropy'], optimizer=optimizer)

        # decoder
        self.decoder = self.build_decoder()
        self.decoder.compile(loss=[face_recognition_loss], optimizer=optimizer)

        img = Input(shape=self.img_shape)
        encoded = self.encoder(img)

        label = Input(shape=(1,))
        decoded = self.decoder([encoded, label])
        # decoded = self.decoder(encoded)

        self.discriminator.trainable = False

        validity = self.discriminator(encoded)

        self.adversarial_autoencoder = Model([img, label], [decoded, validity])
        self.adversarial_autoencoder.compile(loss=[face_recognition_loss, 'binary_crossentropy'],
                                             loss_weights=[0.999, 0.001],
                                             optimizer=optimizer)

    def build_discriminator(self):
        model = models.Sequential()

        model.add(layers.Dense(512, input_dim=self.encoded_dim))
        model.add(layers.LeakyReLU(alpha=0.2))
        model.add(layers.BatchNormalization(momentum=0.8))
        model.add(layers.Dense(512))
        model.add(layers.LeakyReLU(alpha=0.2))
        model.add(layers.BatchNormalization(momentum=0.8))
        model.add(layers.Dense(1, activation='sigmoid'))

        model.summary()

        input = layers.Input(shape=(self.encoded_dim,))
        output = model(input)

        return models.Model(input, output)

    def build_encoder(self):
        # Encoder
        def conv2d(layer_input, filters, f_size=4):
            """Layers used during downsampling"""
            d = Conv2D(filters, kernel_size=f_size, strides=2, padding='same')(layer_input)
            d = LeakyReLU(alpha=0.2)(d)
            d = InstanceNormalization()(d)
            return d

        def deconv2d(layer_input, skip_input, filters, f_size=4, dropout_rate=0):
            """Layers used during upsampling"""
            u = UpSampling2D(size=2)(layer_input)
            u = Conv2D(filters, kernel_size=f_size, strides=1, padding='same', activation='relu')(u)
            if dropout_rate:
                u = Dropout(dropout_rate)(u)
            u = InstanceNormalization()(u)
            u = Concatenate()([u, skip_input])
            return u

            # Image input

        d0 = Input(shape=self.img_shape)

        # Downsampling
        d1 = conv2d(d0, self.gf)
        d2 = conv2d(d1, self.gf * 2)
        d3 = conv2d(d2, self.gf * 4)
        output_img = conv2d(d3, self.gf * 8)
        output_img = layers.Flatten()(output_img)
        encoded = Dense(self.encoded_dim)(output_img)

        Model(d0, encoded).summary()

        return Model(d0, encoded)

    def build_decoder(self):
        noise = Input(shape=(self.encoded_dim,))
        label = Input(shape=(1,), dtype='int32')

        label_embedding = Flatten()(Embedding(self.num_classes, self.encoded_dim)(label))

        model_input = multiply([noise, label_embedding])

        # output_img = model(model_input)

        def conv2d(layer_input, filters, f_size=4):
            """Layers used during downsampling"""
            d = Conv2D(filters, kernel_size=f_size, strides=2, padding='same')(layer_input)
            d = LeakyReLU(alpha=0.2)(d)
            d = InstanceNormalization()(d)
            return d

        def deconv2d(layer_input, filters, f_size=4, dropout_rate=0):
            """Layers used during upsampling"""
            u = UpSampling2D(size=2)(layer_input)
            u = Conv2D(filters, kernel_size=f_size, strides=1, padding='same', activation='relu')(u)
            if dropout_rate:
                u = Dropout(dropout_rate)(u)
            u = InstanceNormalization()(u)
            return u

        # Upsampling
        model_input = layers.Dense(8 * 8 * 256)(model_input)
        model_input = layers.Reshape((8, 8, 256))(model_input)
        u1 = deconv2d(model_input, self.gf * 4)
        u2 = deconv2d(u1, self.gf * 2)
        u3 = deconv2d(u2, self.gf)

        u4 = UpSampling2D(size=2)(u3)
        output_img = Conv2D(self.channels, kernel_size=4, strides=1, padding='same', activation='tanh')(u4)
        Model([noise, label], output_img).summary()

        return Model([noise, label], output_img)

    def train(self, epochs, batch_size=128, save_interval=100):
        # laod data
        (X_train, y_train) = load_data((self.rows, self.cols))

        # rescale
        X_train = (X_train.astype(np.float32) - 127.5) / 127.5
        y_train = y_train.reshape(-1, 1)

        half_batch = int(batch_size) // 2

        for epoch in range(epochs):
            # Train discriminator
            idx = np.random.randint(0, X_train.shape[0], half_batch)
            images, labels = X_train[idx], y_train[idx]

            # encode this images
            encoded_images = self.encoder.predict(images)  # latent fake

            # sample from normal distribution
            latent_real = np.random.normal(size=(half_batch, self.encoded_dim))

            valid = np.ones((half_batch, 1))
            fake = np.zeros((half_batch, 1))

            d_loss_real = self.discriminator.train_on_batch(latent_real, valid)
            d_loss_fake = self.discriminator.train_on_batch(encoded_images, fake)
            d_loss = 0.5 * np.add(d_loss_fake, d_loss_real)

            # Train generator
            idx = np.random.randint(0, X_train.shape[0], half_batch)
            images, labels = X_train[idx], y_train[idx]

            valid_y = np.ones((half_batch, 1))

            g_loss = self.adversarial_autoencoder.train_on_batch([images, labels], [images, valid_y])

            # Plot the progress
            print("%d [D loss: %f, acc: %.2f%%] [G loss: %f, mse: %f]" % (
                epoch, d_loss[0], 100 * d_loss[1], g_loss[0], g_loss[1]))

            # If at save interval => save generated image samples
            if epoch % save_interval == 0:
                # Select a random half batch of images
                idx = np.random.randint(0, X_train.shape[0], 10)
                images, labels = X_train[idx], y_train[idx]
                self.save_imgs(epoch, images, labels)

    def save_imgs(self, epoch, imgs, labels):
        r, c = 2, 5

        encoded_imgs = self.encoder.predict(imgs)
        gen_imgs = self.decoder.predict([encoded_imgs, labels])

        gen_imgs = 0.5 * gen_imgs + 0.5

        fig, axs = plt.subplots(r, c)
        fig.suptitle("CGAN: Generated digits", fontsize=12)
        cnt = 0
        for i in range(r):
            for j in range(c):
                axs[i, j].imshow(gen_imgs[cnt])
                axs[i, j].set_title("{0} - {1}".format(labels[cnt] * 5, (labels[cnt] + 1) * 5))
                axs[i, j].axis('off')
                cnt += 1
        fig.savefig("images/%d.png" % epoch)
        plt.close()

    def save_model(self):

        def save(model, model_name):
            model_path = "aae/saved_model/%s.json" % model_name
            weights_path = "aae/saved_model/%s_weights.hdf5" % model_name
            options = {"file_arch": model_path,
                       "file_weight": weights_path}
            json_string = model.to_json()
            open(options['file_arch'], 'w').write(json_string)
            model.save_weights(options['file_weight'])

        save(self.generator, "aae_generator")
        save(self.discriminator, "aae_discriminator")


if __name__ == '__main__':
    aae = CAAE()
    aae.train(epochs=40000, batch_size=32, save_interval=100)
    aae.save_model()
