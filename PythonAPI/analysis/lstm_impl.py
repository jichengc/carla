import re
import numpy as np
import unidecode
from IPython import get_ipython;
#get_ipython().magic('reset -sf')
import matplotlib.pyplot as plt
import pandas
import math
from keras import metrics
from keras import Input, Model
from keras.models import Sequential
from keras.models import load_model
# from keras.layers import *
from keras.layers import Dense, Dropout, Softmax, Flatten, concatenate
from keras.layers import Activation, TimeDistributed, RepeatVector, Embedding
from keras.layers.recurrent import LSTM
from keras.callbacks import EarlyStopping
from keras.utils import plot_model
from keras.utils import to_categorical
from keras.preprocessing.sequence import pad_sequences
import keras.backend as K # for custom loss function
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error
from sklearn.metrics import mean_absolute_error

from datetime import datetime

class CombinedLSTM(object):
	def __init__(history_shape, goals_position_shape, one_hot_goal_shape, future_shape, hidden_dim, beta):
		traj_input_shape    = (history_shape[1], history_shape[2])
		goal_input_shape    = (goals_position_shape[1],)
		n_outputs           = one_hot_goal_shape[1]
		intent_input_shape  = (n_outputs,)
		future_horizon      = future_shape[1]
		future_dim	        = future_shape[2]

		self.goal_model = GoalLSTM(traj_input_shape, goal_input_shape, n_outputs, hidden_dim=hidden_dim, beta=beta)
		self.traj_model = TrajLSTM(traj_input_shape, intent_input_shape, future_horizon, future_dim, hidden_dim=hidden_dim)

	def fit(train_set, val_set):
		self.goal_model.fit_model(train_set, val_set, num_epochs=100)
		self.traj_model.fit_model(train_set, val_set, num_epochs=100)

	def predict(test_set):
		goal_pred = self.goal_model.predict(test_set)

		# TODO: how to cleanly do multimodal predictions here.  Maybe we can't cleanly just pass a test set, or need to add
		# a new field to the dictionary with top k goal predictions and loop in the predict function.
		traj_pred = self.traj_pred.predict(test_set)
		return goal_pred, traj_pred

	def save(self):
		raise NotImplementedError("XS: TODO")

class GoalLSTM(object):
	"""docstring for GoalLSTM"""
	def __init__(self, traj_input_shape, goal_input_shape, n_outputs, hidden_dim=100, beta=0.1):
		self.beta       = beta
		self.model      = self._create_model(traj_input_shape, goal_input_shape, hidden_dim, n_outputs)
		self.history    = None

		''' Debug '''
		#plot_model(self.model, to_file='goal_model.png')
		#print(self.model.summary())

	def _max_ent_loss(self, y_true, y_pred):
		loss1 = K.categorical_crossentropy(y_true, y_pred)
		loss2 = K.categorical_crossentropy(y_pred, y_pred)
		loss  = loss1 + self.beta * loss2
		return loss

	def _top_k_acc(self, y_true, y_pred, k=3):
		return metrics.top_k_categorical_accuracy(y_true, y_pred, k=3)

	def _create_model(self, traj_input_shape, goal_input_shape, hidden_dim, n_outputs):
		# Input to lstm
		lstm_input = Input(shape=(traj_input_shape),name="input_trajectory")

		# LSTM unit
		lstm = LSTM(hidden_dim,return_state=True,name="lstm_unit")

		# LSTM outputs
		lstm_outputs, state_h, state_c = lstm(lstm_input)

		# Input for goals
		goals_input = Input(shape=(goal_input_shape),name="goal_input")

		# Merge inputs with LSTM features
		concat_input = concatenate([goals_input,lstm_outputs],name="stacked_input")

		concat_output = Dense(100, activation="relu", name="concat_relu")(concat_input)

		# Final FC layer with a softmax activation
		goal_output = Dense(n_outputs,activation="softmax",name="goal_output")(concat_output)
		    
		# Create final model
		model = Model([lstm_input,goals_input], goal_output)

		# Compile model using loss
		#     model.compile(loss='categorical_crossentropy', optimizer='adam', metrics=['accuracy'])
		model.compile(loss=self._max_ent_loss, optimizer='adam', metrics=[self._top_k_acc])

		return model

	def fit_model(self, train_set, val_set, num_epochs=100):
		val_data = ([val_set['history_traj_data'], val_set['goal_position']], \
					 val_set['one_hot_goal'])

		self.history = self.model.fit(\
					[train_set['history_traj_data'], train_set['goal_position']], \
					train_set['one_hot_goal'], \
					epochs=num_epochs, 
					validation_data=val_data)

	def plot_history(self):
		if not self.history:
			raise AttributeError("No history available.  Run fit_model.")

		plt.plot(self.history.history['top_k_acc'])
		plt.plot(self.history.history['val_top_k_acc'])
		plt.title('model accuracy')
		plt.ylabel('accuracy')
		plt.xlabel('epoch')
		plt.legend(['train', 'validation'], loc='lower right')
		plt.show()
		# summarize history for loss
		plt.plot(self.history.history['loss'])
		plt.plot(self.history.history['val_loss'])
		plt.title('model loss')
		plt.ylabel('loss')
		plt.xlabel('epoch')
		plt.legend(['train', 'validation'], loc='upper right')
		plt.show()

	def save_model(self):
		if not self.history:
			raise AttributeError("No history available.  Run fit_model.")

		now = datetime.now()
		dt_string = now.strftime('%m_%d_%H_%M')
		file_name = "goal_model_%.4f_%s.h5" % (self.goal_history.history['val_top_k_acc'][-1], dt_string)
		self.model.save(file_name)
		print("Saved goal model to disk")

	def predict(self, test_set):
		goal_pred = self.model.predict([test_set['history_traj_data'], test_set['goal_position']])
		return goal_pred

class TrajLSTM(object):
	"""docstring for TrajLSTM"""
	def __init__(self, traj_input_shape, intent_input_shape, future_horizon, future_dim, hidden_dim=100):
		self.model = self._create_model(traj_input_shape, intent_input_shape, hidden_dim, future_horizon, future_dim)
		self.history    = None

		''' Debug '''
		# plot_model(self.model,to_file='traj_model.png')
		# print(self.model.summary())

	def _create_model(self, traj_input_shape, intent_input_shape, hidden_dim, future_horizon, future_dim):

		# Input to lstm
		lstm_input = Input(shape=(traj_input_shape),name="trajectory_input")

		# LSTM unit
		lstm = LSTM(hidden_dim,return_state=True,name="lstm_unit")

		# LSTM outputs
		lstm_outputs, state_h, state_c = lstm(lstm_input)
		encoder_states = [state_h,state_c]

		# Input for goals
		goals_input = Input(shape=(intent_input_shape),name="goal_input")

		# Repeat the goal inputs
		goals_repeated= RepeatVector(future_horizon)(goals_input)

		# Define decoder
		decoder = LSTM(hidden_dim,return_sequences=True, return_state=True)

		# Decoder outputs, initialize with previous lstm states
		decoder_outputs,_,_ = decoder(goals_repeated,initial_state=encoder_states)

		# Shape to a time series prediction of future_horizon x features
		decoder_fully_connected = TimeDistributed(Dense(future_dim))(decoder_outputs)

		# Create final model
		model = Model([lstm_input,goals_input], decoder_fully_connected)

		# Compile model using loss
		model.compile(loss='mean_squared_error', optimizer='adam', metrics=['accuracy'])

		return model

	def fit_model(self, train_set, val_set, num_epochs=100):
		val_data = ([val_set['history_traj_data'], val_set['one_hot_goal']], \
					 val_set['future_traj_data'])
		
		self.history = self.model.fit(\
					[train_set['history_traj_data'], train_set['one_hot_goal']], \
					train_set['future_traj_data'], \
					epochs=num_epochs, 
					validation_data=val_data)

	def plot_history(self):
		if not self.history:
			raise AttributeError("No history available.  Run fit_model.")

		plt.plot(self.history.history['acc'])
		plt.plot(self.history.history['val_acc'])
		plt.title('model accuracy')
		plt.ylabel('accuracy')
		plt.xlabel('epoch')
		plt.legend(['train', 'validation'], loc='lower right')
		plt.show()
		# summarize history for loss
		plt.plot(self.history.history['loss'])
		plt.plot(self.history.history['val_loss'])
		plt.title('model loss')
		plt.ylabel('loss')
		plt.xlabel('epoch')
		plt.legend(['train', 'validation'], loc='upper right')
		plt.show()

	def save_model(self):
		if not self.history:
			raise AttributeError("No history available.  Run fit_model.")

		now = datetime.now()
		dt_string = now.strftime('%m_%d_%H_%M')
		file_name = "traj_model_%.4f_%s.h5" % (self.history.history['val_acc'][-1], dt_string)
		self.model.save(file_name)
		print("Saved traj model to disk")

	def predict(self, test_set):
		# TODO: how to incorporate goal prediction
		traj_pred = self.model.predict([test_set['history_traj_data'], test_set['one_hot_goal']])
		return traj_pred