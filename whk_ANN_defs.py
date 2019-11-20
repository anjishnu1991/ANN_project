#To time the code
import time

timeStart = time.time()

#Loading base packages
import os
import math
import sys
import configparser as cfg

#Loading Keras
import keras
from keras.models import Sequential, Model
from keras.layers import Dense, Input, BatchNormalization, Dropout
from keras import metrics
from keras.optimizers import SGD
from keras.losses import binary_crossentropy

#Loading sklearn for data processing & analysis
import sklearn as skl
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_curve, auc
from sklearn.preprocessing import StandardScaler

#Loading the packages for handling the data
import uproot as ur
import pandas 
import numpy as np

#Loading packages needed for plottting
import matplotlib.pyplot as plt

#Defining colours for the plots
#The colours were chosen using the xkcd guice
#color_tW = '#66FFFF'
color_tW = '#0066ff'
#color_tt = '#FF3333'
color_tt = '#990000'
color_sys = '#009900'
color_tW2 = '#02590f'
color_tt2 = '#FF6600'

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


#Setting up the output directories
#output_path = '/cephfs/user/s6chkirf/whk_adversarialruns_' + sys.argv[1] + '_' + sys.argv[2] + '_' + sys.argv[3] + '_' + sys.argv[4] + '_' + sys.argv[5] + '_' +sys.argv[6] + '_' + sys.argv[7] + '/'
output_path = 'output/'
array_path = output_path + 'arrays/'
if not os.path.exists(output_path):
	os.makedirs(output_path)
if not os.path.exists(array_path):
	os.makedirs(array_path)


plt.ticklabel_format(style='sci', axis='x', scilimits=(0,0))
plt.ticklabel_format(style='sci', axis='y', scilimits=(0,0))

#------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
#This is the main class for the adversarial neural network setup
class ANN_environment(object):

	def __init__(self):
		""" opens files, loads config and initializes variables """
		#load the config
		self.config_path = "config_whk_ANN.ini"
		self.config = cfg.ConfigParser()
		try:
			self.config.read(self.config_path)
		except:
			print('[WARNING] No config file found. Using defaults')
			self.config.read("default_whk_ANN.ini")

		#TODO load additional config for HTCondor/BAF

		#A list of more general settings
		self.variables = np.array(["mass_lep1jet2", "pTsys_lep1lep2met", "pTsys_jet1jet2", "mass_lep1jet1", "deltapT_lep1_jet1", "deltaR_lep1_jet2", "deltaR_lep1lep2_jet2", "mass_lep2jet1", "pT_jet2", "deltaR_lep1_jet1", "deltaR_lep1lep2_jet1jet2met", "deltaR_lep2_jet2", "cent_lep2jet2", "deltaR_lep2_jet1"])
		#The seed is used to make sure that both the events and the labels are shuffeled the same way because they are not inherently connected.
		self.seed = int(self.config['General']['Seed'])
		#All information necessary for the input
		#The exact data and targets are set late
		self.input_path = self.config['General']['InputPath']
		self.signal_sample = self.config['General']['SignalSample']
		self.background_sample = self.config['General']['BackgroundSample']
		self.systematics_sample = self.config['General']['SystematicsSample']
		self.signal_tree = ur.open(self.input_path)[self.signal_sample]
		self.background_tree = ur.open(self.input_path)[self.background_sample]
		self.systematic_tree = ur.open(self.input_path)[self.systematics_sample]
		self.sample_training = None
		self.sample_validation = None
		self.adversarial_training = None
		self.adversarial_validation = None
		self.target_training = None
		self.target_validation = None
		self.target_systematic = None
		#Dimension of the variable input used to define the size of the first layer
		self.input_dimension = self.variables.shape
		#These arrays are used to save loss and accuracy of the two networks
		#That is also important to later be able to use the plotting software desired. matplotlib is not the best tool at all times
		self.discriminator_history_array = []
		self.adversary_history_array = []
		self.model_history_array = []
		self.discriminator_history = None
		#Here are the definitions for the two models
		#All information for the length of the training. Beware that epochs might only come into the pretraining
		#Iterations are used for the adversarial part of the training
		#original: 10 10 1000
		self.discriminator_epochs = int(self.config['Training']['DiscriminatorEpochs'])
		self.adversary_epochs = int(self.config['Training']['AdversaryEpochs'])
		self.training_iterations = int(self.config['Training']['TrainingIterations'])
		#Setup of the networks, nodes and layers
		self.discriminator_layers = int(self.config['Network']['DiscriminatorLayers'])
		self.discriminator_nodes = int(self.config['Network']['DiscriminatorNodes'])
		self.adversary_layers = int(self.config['Network']['AdversaryLayers'])
		self.adversary_nodes = int(self.config['Network']['AdversaryNodes'])
		#Setup of the networks, loss and optimisation
		#original lr/momentum sys.argv 123456
		#0.01 0.001 0.01 0.3 0.3 0.3 0.5
		self.discriminator_optimizer = SGD(lr = float(self.config['Network']['DiscriminatorLearningRate']), momentum = float(self.config['Network']['DiscriminatorMomentum']))
		self.discriminator_dropout = float(self.config['Network']['DiscriminatorDropout'])
		self.discriminator_inputdropout = float(self.config['Network']['DiscriminatorInputDropout'])
 		#self.discriminator_loss = binary_crossentropy

		self.adversary_optimizer = SGD(lr = float(self.config['Network']['AdversaryLearningRate']), momentum = float(self.config['Network']['AdversaryMomentum']))
		self.adversary_dropout = float(self.config['Network']['AdversaryDropout'])
 		#self.adversary_loss = binary_crossentropy

		self.combined_optimizer = SGD(lr = float(self.config['Network']['CombinedLearningRate']), momentum = float(self.config['Network']['CombinedMomentum']))

		self.validation_fraction = float(self.config['Training']['ValidationFraction'])

		#The following set of variables is used to evaluate the result
		#fpr = false positive rate, tpr = true positive rate
		self.tpr = 0.
		self.fpr = 0.
		self.threshold = 0.
		self.auc = 0.

		self.lambda_value = float(self.config['Training']['LambdaValue'])

		self.time_list = []
		self.time_predisc = 0.

	def initialize_sample(self):
		"""
		Initializing the data and target samples
		The split function cuts into a training sample and a test sample
		Important note: Have to use the same random seed so that event and target stay in the same order as we shuffle
		"""
		#Signal and background are needed for the classification task, signal and systematic for the adversarial part
		#In this first step the events are retrieved from the tree, using the chosen set of variables
		#The numpy conversion is redundant
		self.events_signal = self.signal_tree.pandas.df(self.variables).to_numpy()
		self.events_background = self.background_tree.pandas.df(self.variables).to_numpy()
		self.events_systematic = self.systematic_tree.pandas.df(self.variables).to_numpy()
		#Setting up the weights. The weights for each tree are stored in 'weight_nominal'
		self.weight_signal = self.signal_tree.pandas.df('weight_nominal').to_numpy()
		self.weight_background = self.background_tree.pandas.df('weight_nominal').to_numpy()
		self.weight_systematic = self.systematic_tree.pandas.df('weight_nominal').to_numpy()
		#Rehsaping the weights
		self.weight_signal = np.reshape(self.weight_signal, (len(self.events_signal), 1))
		self.weight_background = np.reshape(self.weight_background, (len(self.events_background), 1))
		#original: sys.argv[7]
		self.weight_background_adversarial = self.weight_background * 0.5
		self.weight_systematic = np.reshape(self.weight_systematic, (len(self.events_systematic), 1))
		#Normalisation to the eventcount can be used instead of weights, especially if using data
		self.norm_signal = np.reshape([1./float(len(self.events_signal)) for x in range(len(self.events_signal))], (len(self.events_signal), 1))
		self.norm_background = np.reshape([1./float(len(self.events_background)) for x in range(len(self.events_background))], (len(self.events_background), 1))
		#Calculating the weight ratio to scale the signal weight up. This tries to take the high amount of background into account
		self.weight_ratio = ( self.weight_signal.sum() + self.weight_systematic.sum() )/ self.weight_background.sum()
		self.weight_signal = self.weight_signal / self.weight_ratio
		self.weight_systematic = self.weight_systematic / self.weight_ratio

		#Setting up the targets
		#target combined is used to make sure the systematics are seen as signal for the first net in the combined training
		self.target_signal = np.reshape([1 for x in range(len(self.events_signal))], (len(self.events_signal), 1))
		self.target_background = np.reshape([0 for x in range(len(self.events_background))], (len(self.events_background), 1))
		self.target_systematic = np.reshape([1 for x in range( len( self.events_systematic))], (len(self.events_systematic), 1))
		self.target_systematic_adversarial = np.reshape([0 for x in range( len( self.events_systematic))], (len(self.events_systematic), 1))
		self.target_background_adversarial = np.reshape( np.random.randint(2, size =len( self.events_background)), ((len(self.events_background)), 1))
		#The samples and corresponding targets are now split into a sample for training and a sample for testing. Keep in mind that the same random seed should be used for both splits
		self.sample_training, self.sample_validation = train_test_split(np.concatenate((self.events_signal, self.events_background, self.events_systematic)), test_size = self.validation_fraction, random_state = self.seed)
		self.target_training, self.target_validation = train_test_split(np.concatenate((self.target_signal, self.target_background, self.target_systematic)), test_size = self.validation_fraction, random_state = self.seed)
		self.target_adversarial, self.target_adversarial_validation = train_test_split(np.concatenate((self.target_signal, self.target_background_adversarial, self.target_systematic_adversarial)), test_size = self.validation_fraction, random_state = self.seed)
		#Splitting the weights
		self.weight_training, self.weight_validation = train_test_split(np.concatenate((self.weight_signal, self.weight_systematic, self.weight_background)), test_size = self.validation_fraction, random_state = self.seed)
		self.weight_adversarial, self.weight_adversarial_validation = train_test_split(np.concatenate((self.weight_signal, self.weight_systematic, self.weight_background_adversarial)), test_size = self.validation_fraction, random_state = self.seed)
		self.norm_training, self.norm_validation = train_test_split(np.concatenate((self.norm_signal, self.norm_background)), test_size = self.validation_fraction, random_state = self.seed)

		#Setting up a scaler
		#A scaler makes sure that all variables are normalised to 1 and have the same order of magnitude for that reason
		scaler = StandardScaler()
		self.sample_training = scaler.fit_transform(self.sample_training)
		self.sample_validation = scaler.fit_transform(self.sample_validation)      	

#----------------------------------------------------------------------------------------------------------------------------------------
#Here the discriminator is built
#It has an input layer fit to the shape of the variables
#A loop creates the desired amount of deep layers
#It ends in a single sigmoid layer
#Additionally the last layer is saved to be an optional input to the adversary
	def build_discriminator(self):
		#The discriminator aims to separate signal and background
		#There is an input layer after which the desired amount of hidden layers is added in a loop
		#In the loop normalisation and dropout are added too

		self.network_input = Input( shape = (self.input_dimension) )
		self.layer_discriminator = Dense( self.discriminator_nodes, activation = "elu")(self.network_input)
		self.layer_discriminator = BatchNormalization()(self.layer_discriminator)
		#(experimental) Idea: High dropout in the first layer effectively regularizes variables. Untested.
		self.layer_discriminator = Dropout(self.discriminator_inputdropout)(self.layer_discriminator)
		for _ in range(self.discriminator_layers -1):
			#Placeholder iterator name so pylint doesn't complain
			self.layer_discriminator = Dense(self.discriminator_nodes, activation = "elu")(self.layer_discriminator)
			self.layer_discriminator = BatchNormalization()(self.layer_discriminator)
			self.layer_discriminator = Dropout(self.discriminator_dropout)(self.layer_discriminator)
		self.layer_discriminator = Dense( 1, activation = "sigmoid")(self.layer_discriminator)

		self.model_discriminator = Model(inputs = [self.network_input], outputs = [self.layer_discriminator])
		self.model_discriminator.compile(loss = "binary_crossentropy", weighted_metrics = [metrics.binary_accuracy], optimizer = self.discriminator_optimizer)
		self.model_discriminator.summary()

#Here the adversary is built
#It uses the discriminator output as inputobject has no attribute 'append'

#Optionally the last layer can be used additionally     
#In a loop the deep layers are created
#It ends in a single sigmoid layer
	def build_adversary(self):
		#This is where the adversary is initialized
		#It is just another classifier

		self.adversary_input = Input( shape = (self.input_dimension) )
		self.layer_adversary = self.model_discriminator(self.network_input)
		self.layer_adversary = Dense( self.adversary_nodes, activation = 'elu')(self.layer_adversary)
		self.layer_adversary = BatchNormalization()(self.layer_adversary)
		self.layer_adversary = Dropout(self.adversary_dropout)(self.layer_adversary)
		for _ in range(self.adversary_layers - 1):
			self.layer_adversary = Dense(self.adversary_nodes, activation = "elu")(self.layer_adversary)
			self.layer_adversary = BatchNormalization()(self.layer_adversary)
			self.layer_adversary = Dropout(self.adversary_dropout)(self.layer_adversary)
		self.layer_adversary = Dense( 1, activation = "sigmoid")(self.layer_adversary)

		self.model_adversary = Model(inputs = [self.network_input], outputs = [self.layer_adversary])
		self.model_adversary.compile(loss = "binary_crossentropy", optimizer = self.adversary_optimizer)
		self.model_adversary.summary()

	def build_combined_training(self):
		#The discriminator and adversary are added up to a single model running on a combined loss function

		def make_losses_adversary():
			def losses_adversary(y_true, y_pred):
				return self.lambda_value * binary_crossentropy(y_true, y_pred)
			return losses_adversary

		self.model_combined = Model(inputs = self.adversary_input, outputs = [self.model_discriminator(self.adversary_input), self.model_adversary(self.adversary_input)])
		#Compiling a model with multiple loss functions lets Keras use the sum by default
		self.model_combined.compile( loss = ['binary_crossentropy', make_losses_adversary()], optimizer = self.combined_optimizer)   


	
	def run_adversarial_training(self):

		losses_test = {"L_f": [], "L_r": [], "L_f - L_r": []}
		losses_train = {"L_f": [], "L_r": [], "L_f - L_r": []}

		def make_trainable(network, flag):
			network.trainable = flag
			for l in network.layers:
				l.trainable = flag
			network.compile

		for iteration in range(self.training_iterations):

			time_temp = time.time()

			print('Running training: Iteration ' + str(iteration+1) + ' of ' + str(self.training_iterations))

			#Only save losses every 5 iterations
			if iteration % 5 == 0 or iteration == (self.training_iterations):
				self.save_losses(iteration, self.model_combined, losses_test, losses_train)

			make_trainable(self.model_discriminator, True)
			make_trainable(self.model_adversary, False)

			self.model_history = self.model_combined.fit(self.sample_training, [self.target_training, self.target_adversarial], epochs=1, batch_size = int(self.config['Training']['BatchSize']), sample_weight = [self.weight_training.ravel(),self.weight_adversarial.ravel()])
			self.model_history_array.append(self.model_history)

			make_trainable(self.model_discriminator, False)
			make_trainable(self.model_adversary, True)

			self.adversary_history = self.model_adversary.fit(self.sample_training, self.target_adversarial, epochs=1, batch_size = int(self.config['Training']['BatchSize']), sample_weight = self.weight_training.ravel())
			self.adversary_history_array.append(self.adversary_history)

			self.time_list.append(time.time() - time_temp)


	def pretrain_adversary(self):

		self.model_adversary.fit(self.sample_training, self.target_adversarial.ravel(), epochs = self.adversary_epochs, batch_size = int(self.config['Training']['BatchSize']), sample_weight = self.weight_adversarial.ravel())






	def pretrain_discriminator(self):

		time_temp = time.time()
		
		print('Pretraining discriminator with ' + str(self.discriminator_epochs) + ' epochs.')

		#print(self.target_training[12:500])
		#print(self.target_training[-1:-100])

		self.model_discriminator.summary()

		self.discriminator_history = self.model_discriminator.fit(self.sample_training, self.target_training.ravel(), epochs=self.discriminator_epochs, batch_size = int(self.config['Training']['BatchSize']), sample_weight = self.weight_training.ravel(), validation_data = (self.sample_validation, self.target_validation, self.weight_validation.ravel()))
		self.discriminator_history_array.append(self.discriminator_history)
		print(self.discriminator_history.history.keys())

		self.time_predisc = time.time() - time_temp

		#for training_iteration in range(self.training_iterations):
		#    discriminator_history = self.model_discriminator.fit(self.sample_training, self.target_training, epochs=self.discriminator_epochs, validation_data = (self.sample_validation, self.target_validation))
		#    adversary_history = self.model_combined.fit(self.adversarial_training, [self.combined_target, self.adversarial_target], epochs=self.adversary_epochs)

	def predict_model(self):

		self.model_prediction = self.model_discriminator.predict(self.sample_validation).ravel()
		self.fpr, self.tpr, self.threshold = roc_curve(self.target_validation, self.model_prediction)
		self.auc = auc(self.fpr, self.tpr)

		self.adversary_prediction = self.model_adversary.predict(self.sample_validation).ravel()
		self.adversary_fpr, self.adversary_tpr, self.adversary_threshold = roc_curve(self.target_adversarial_validation, self.adversary_prediction)
		self.adversary_auc = auc(self.adversary_fpr, self.adversary_tpr)

		print('Discriminator AUC', self.auc)
		print('Adversary AUC', self.adversary_auc)

#----------------------------------------------------------------------------------------------Plot structure------------------------------------------------------------------------------------------------------------
	def plot_losses(self, i, l_test, l_train):

		ax1 = plt.subplot(311)   
		values_test = np.array(l_test["L_f"])
		values_train = np.array(l_train["L_f"])
		plt.plot(range(len(values_test)), values_test, label=r"$Loss_{net1}^{test}$", color="blue", linestyle='dashed')
		plt.plot(range(len(values_train)), values_train, label=r"$Loss_{net1}^{train}$", color="blue")
		plt.legend(loc="upper right", prop={'size' : 7})
		plt.legend(frameon=False)
		plt.grid()
		ax1.ticklabel_format(style='sci', axis='both', scilimits=(0,0))
		
		ax2 = plt.subplot(312, sharex=ax1) 
		values_test = np.array(l_test["L_r"])
		values_train = np.array(l_train["L_r"])
		plt.plot(range(len(values_test)), values_test, label=str(self.lambda_value)+r"$ \cdot Loss_{net2}^{test}$", color="green", linestyle='dashed')
		plt.plot(range(len(values_train)), values_train, label=str(self.lambda_value)+r"$ \cdot Loss_{net2}^{train}$", color="green")
		plt.legend(loc="upper right", prop={'size' : 7})
		plt.legend(frameon=False)
		plt.ylabel('Loss')
		plt.grid()
		ax2.ticklabel_format(style='sci', axis='both', scilimits=(0,0))
		
		ax3 = plt.subplot(313, sharex=ax1)
		values_test = np.array(l_test["L_f - L_r"])
		values_train = np.array(l_train["L_f - L_r"])
		plt.plot(range(len(values_test)), values_test, label=r"$Loss_{net1}^{test} - $"+str(float(self.lambda_value))+r"$ \cdot Loss_{net2}^{test}$", color="red", linestyle='dashed')  
		plt.plot(range(len(values_train)), values_train, label=r"$Loss_{net1}^{train} - $"+str(float(self.lambda_value))+r"$ \cdot Loss_{net2}^{train}$", color="red")  
		plt.legend(loc="upper right", prop={'size' : 7})
		plt.grid()
		ax3.ticklabel_format(style='sci', axis='both', scilimits=(0,0))
		plt.xlabel('Epoch')
		
		plt.legend(frameon=False)
		plt.gcf().savefig(output_path + 'losses_' + str(i/5) + '.png')
		plt.gcf().clear()

#losses_test = {"L_f": [], "L_r": [], "L_f - L_r": []}
#losses_train = {"L_f": [], "L_r": [], "L_f - L_r": []}

	def save_losses(self, i, network, lossestest, lossestrain):
		l_test = network.evaluate(self.sample_training, [self.target_training, self.target_adversarial], batch_size = int(self.config['Training'] ['BatchSize']))
		l_train = network.evaluate(self.sample_validation, [self.target_validation, self.target_adversarial_validation], batch_size = int(self.config['Training'] ['BatchSize']))
		lossestest["L_f"].append(l_test[1])
		lossestest["L_r"].append(-l_test[2])
		lossestest["L_f - L_r"].append(l_test[0])
		lossestrain["L_f"].append(l_train[1])
		lossestrain["L_r"].append(-l_train[2])
		lossestrain["L_f - L_r"].append(l_train[0])
		#DEBUG FLOAT ERROR
#		with open('float_error.txt','a') as f:
#			print('Next Iteration', file=f)
#			print('\tl_test: ', l_test, file=f)
#			print('\tL_f: ', lossestest["L_f"][-1], file=f)
#			print('\tL_r: ', lossestest['L_r'][-1], file=f)
#			print('\tL_f-L_r: ', lossestest['L_f - L_r'][-1], file=f)
		if i % 5 == 0 or i == (self.training_iterations):
			self.plot_losses(i, lossestest, lossestrain)



	def plot_roc(self):
		plt.title('Receiver Operating Characteristic')
		plt.plot(self.fpr, self.tpr, 'g--', label='$AUC_{train}$ = %0.2f'% self.auc)
		plt.legend(loc='lower right')
		plt.plot([0,1],[0,1],'r--')
		plt.xlim([-0.,1.])
		plt.ylim([-0.,1.])
		plt.ylabel('True Positive Rate', fontsize='large')
		plt.xlabel('False Positive Rate', fontsize='large')
		plt.legend(frameon=False)
		#plt.show()
		plt.gcf().savefig(output_path + 'roc.png')
		#plt.gcf().savefig(output_path + 'simple_ROC_' + file_extension + '.eps')
		plt.gcf().clear()

	def plot_separation(self):
		self.signal_histo = []
		self.background_histo = []
		for i in range(len(self.sample_validation)):
			if self.target_validation[i] == 1:
				self.signal_histo.append(self.model_prediction[i])
			if self.target_validation[i] == 0:
				self.background_histo.append(self.model_prediction[i])
				
		plt.hist(self.signal_histo, range=[0., 1.], linewidth = 2, bins=30, histtype="step", density = True, color=color_tW, label = "Signal")
		plt.hist(self.background_histo, range=[0., 1.], linewidth = 2, bins=30, histtype="step", density = True, color=color_tt, label = "Background")
#        plt.hist(self.model_prediction[self.target_training.tolist() == 0], range=[0., 1.], linewidth = 2, bins=30, histtype="step", normed=1, color=color_tt)
#        plt.hist(predicttest__ANN[test_target == 1],   range=[xlo, xhi], linewidth = 2, bins=bins, histtype="step", normed=1, color=color_tW2, label='$Sig_{test}$', linestyle='dashed')
#        plt.hist(predicttest__ANN[test_target == 0],   range=[xlo, xhi], linewidth = 2, bins=bins, histtype="step", normed=1, color=color_tt2, label='$Bkg_{test}$', linestyle='dashed')
#        plt.ylim(0, plt.gca().get_ylim()[1] * float(Options['yScale']))
		plt.legend()
		plt.xlabel('Network response', horizontalalignment='left', fontsize='large')
		plt.ylabel('Event fraction', fontsize='large')
		plt.legend(frameon=False)
		#plt.title('Normalised')
#        plt.gcf().savefig(output_path + 'ANN_NN_' + file_extension + '.png')
		plt.gcf().savefig(output_path + 'separation_discriminator.png')
		#plt.show()
		plt.gcf().clear()


	def plot_separation_adversary(self):
		plt.title('Adversary Response')
		axis1 = plt.subplot(211)
		self.nominal_histo = []
		self.systematic_histo = []
		for i in range(len(self.sample_validation)):
			if self.target_adversarial_validation[i] == 1 and self.target_validation[i] == 1:
				self.nominal_histo.append(self.model_prediction[i])
			if self.target_adversarial_validation[i] == 0 and self.target_validation[i] == 1:
				self.systematic_histo.append(self.model_prediction[i])
				
		ns1, bins1, patches1 = plt.hist(self.nominal_histo, range=[0., 1.], linewidth = 2, bins=30, histtype="step", density = True, color=color_tW, label = "Nominal")
		ns2, bins2, patches2 = plt.hist(self.systematic_histo, range=[0., 1.], linewidth = 2, bins=30, histtype="step", density = True, color=color_sys, label = "Systematics")
#        plt.hist(self.model_prediction[self.target_training.tolist() == 0], range=[0., 1.], linewidth = 2, bins=30, histtype="step", normed=1, color=color_tt)
#        plt.hist(predicttest__ANN[test_target == 1],   range=[xlo, xhi], linewidth = 2, bins=bins, histtype="step", normed=1, color=color_tW2, label='$Sig_{test}$', linestyle='dashed')
#        plt.hist(predicttest__ANN[test_target == 0],   range=[xlo, xhi], linewidth = 2, bins=bins, histtype="step", normed=1, color=color_tt2, label='$Bkg_{test}$', linestyle='dashed')
#        plt.ylim(0, plt.gca().get_ylim()[1] * float(Options['yScale']))
		plt.legend()
		plt.ylabel('Event fraction', fontsize='large')
		plt.legend(frameon=False)


		ratioArray = []
		for iterator in range(len(ns1)):
			if ns1[iterator] > 0:
				ratioArray.append(ns2[iterator]/ns1[iterator])
			else:
				ratioArray.append(1.)

		axis2 = plt.subplot(212, sharex = axis1)
#        axis2.set_ylim([0., 2.])
		plt.plot(bins1[:-1], ratioArray, color = "blue", drawstyle = 'steps-mid')
#        plt.plot(bins1[:-1], ratioArray, color = "blue", marker = "_", linestyle = 'None', markersize = 12)
		plt.hlines(1, xmin = -0.0, xmax = 1.0)
		plt.xlabel('Network response', horizontalalignment='left', fontsize='large')
		#plt.title('Normalised')
#        plt.gcf().savefig(output_path + 'ANN_NN_' + file_extension + '.png')
		plt.gcf().savefig(output_path + 'separation_adversary.png')
		#plt.show()
		plt.gcf().clear()


	def plot_accuracy(self):
		plt.plot(self.model_history.history['binary_accuracy'])
		plt.plot(self.model_history.history['val_binary_accuracy'])
		plt.title('model accuracy')
		plt.ylabel('accuracy')
		plt.xlabel('epoch')
		plt.legend(['train', 'test'], loc='upper left')
		#plt.show()
		plt.gcf().savefig(output_path + 'acc.png')
		plt.gcf().clear()

	def get_speed(self):
		with open('whk_Benchmark.txt','w') as f:
			#f.write('Tensorflow ', tensorflow.__version__, '\n')
			f.write('Keras ', keras.__version__, '\n')
			f.write('Batch size: ', self.config['Training']['BatchSize'], '\n')
			f.write('Discriminator epochs: ', self.config['Training']['DiscriminatorEpochs'], '\n')
			f.write('Iterations: ', self.config['Training']['TrainingIterations'], '\n')
			f.write('Time for Discriminator pretrain: %.3f\n' % (self.time_predisc))
			f.write('Time for all Iterations: %.3f\n' % (sum(self.time_list)))
			f.write('Average time per Epoch: %.3f\n' % (sum(self.time_list)/len(self.time_list)))

#In the following options and variables are read in
#This is done to keep the most important features clearly represented

#with open('/cephfs/user/s6chkirf/whk_ANN_variables.txt','r') as varfile:
with open('whk_ANN_variables.txt','r') as varfile:
	variableList = varfile.read().splitlines() 

print(variableList)


#first_training = ANN_environment(variables = variableList)

first_training = ANN_environment()
first_training.initialize_sample()
first_training.build_discriminator()
first_training.build_adversary()
first_training.build_combined_training()
first_training.pretrain_discriminator()
#first_training.predict_model()
first_training.run_adversarial_training()
first_training.predict_model()
first_training.plot_roc()
first_training.plot_separation()
first_training.plot_separation_adversary()
#first_trainings.plot_separation_adversary()
#first_training.plot_losses()
first_training.get_speed()

timeTotal = time.time() - timeStart
tmins, tsecs = divmod(timeTotal, 60)
thours, tmins = divmod(tmins, 60)

print('Total time was %.3f seconds. (%f:%2f:%2f)' % ((time.time() - timeStart), thours, tmins, tsecs))
with open('whk_Benchmark.txt','a') as f:
	f.write('Total time was %.3f seconds. (%f:%2f:%2f)' % ((time.time() - timeStart), thours, tmins, tsecs))