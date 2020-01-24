import numpy as np
import scipy.io as sio
from utils import fix_angle, generate_image, generate_image_ego
import pdb
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import copy

# Function to get all possible goals, along with occupancy.
# Each goal is an array [x,y,free].
def extract_goals(res_dict):
    # Extract goal options
    occupied_spots = []
    for static_vehicle in res_dict['vehicle_object_lists'][0]:
        xv = round(static_vehicle['position'][0], 2)
        yv = round(static_vehicle['position'][1], 2)
        occupied_spots.append([xv, yv])
    occupied_spots = np.array(occupied_spots)

    goals = []
    xg = sorted(set(occupied_spots[:,0]))
    yg = sorted(set(occupied_spots[:,1]))
    for idx, x in enumerate(xg):
        for y in yg:
            if idx == 0 or idx == len(xg) - 1:
                continue # we are limiting in this case to the middle two parking columns
            diffs = np.sum(np.square( occupied_spots - np.array([x,y]) ), axis= 1)
            if np.min(diffs) < 1e-6:
                goals.append([x,y,0]) # near a vehicle, hence occupied
            else:
                goals.append([x,y,1]) # free
    goals = np.array(goals)
    return goals

# Function to get the control information for ego.
def extract_control_info(res_dict):
    ts = [];
    speeds = [];
    accs = [];
    oris = [];
    throttles = []; steers = []; brakes = []
    hand_brakes = []; reverses = []; gears = [];
    manual_gear_shifts = []
    
    for entry in res_dict['ego_control_list']:
        ts.append(entry['time'])
        speeds.append(entry['velocity'])
        accs.append(entry['acceleration'])
        oris.append(entry['orientation'])
        throttles.append(entry['throttle'])
        steers.append(entry['steer'])
        brakes.append(entry['brake'])
        hand_brakes.append(entry['hand_brake'])
        reverses.append(entry['reverse'])
        gears.append(entry['gear'])
        manual_gear_shifts.append(entry['manual_gear_shift'])
    
    ego_control_dict = {}
    ego_control_dict['t'] = np.array(ts)
    ego_control_dict['speed'] = np.array(speeds)
    ego_control_dict['acceleration'] = np.array(accs)
    ego_control_dict['orientation'] = np.array(oris)
    ego_control_dict['throttle'] = np.array(throttles)
    ego_control_dict['steer'] = np.array(steers)
    ego_control_dict['brake'] = np.array(brakes)
    ego_control_dict['gear'] = np.array(gears)
    ego_control_dict['hand_brakes'] = np.array(hand_brakes).astype(np.int)
    ego_control_dict['reverse'] = np.array(reverses).astype(np.int)
    ego_control_dict['manual_gear_shift'] = np.array(manual_gear_shifts).astype(np.int)
    
    return ego_control_dict

# Function to determine signed forward velocity based on gear but also with outlier detection.
# It is possible to reverse while moving forward or vice-versa (although we hope rare).
def get_longitudinal_velocity(time, vx, vy, v_prev, ego_control_dict):
    speed = np.sqrt(vx**2 + vy**2)
    gear_ind = np.argmax(time <= ego_control_dict['t'] )
    
    # Based on the gear, we can guess the sign of the forward velocity.
    vsign_from_gear = -1.0 if ego_control_dict['reverse'][gear_ind] > 0 else 1.0 
    vel_from_gear = vsign_from_gear * speed
   
    # If the velocity is very small or is consistent with the previous result, use the gear-based vel.
    # Else check for outliers.
    if speed < 0.05 or v_prev is None or vsign_from_gear == np.sign(v_prev):
        return vel_from_gear # vel signs match up or no history to go off of.
    else:
        # Outlier detection - possible to have forward speed in reverse and vice-versa.
        vdiff_opp_gear = np.abs(-vel_from_gear - v_prev)
        vdiff_with_gear = np.abs(vel_from_gear - v_prev)
         
        if vdiff_opp_gear < vdiff_with_gear:
            return -vel_from_gear
        else:
            return vel_from_gear
        
# Function to get full state and intent (goal index) trajectory for a single demonstration.
def extract_full_trajectory(res_dict, goals, prune_start=True, prune_end=True, min_vel_thresh=0.01, exclude_collisions=False):
    '''
    prune_start: if True, remove non-moving portion of data at the start
    prune_end:   if True, remove non-moving portion of data at the end
    min_vel_thresh: minimum velocity (m/s) above which vehicle is moving
    exclude_collisions: Raises an error if collision detected.
    '''
    # Extract intention signal time (first button press) 
    intention_time = res_dict['intention_time_list'][0]

    # See if there were any collisions and return error if exclude_collisions is True.
    if len(res_dict['ego_collision_list']) > 0:
        print('Collisions encountered: ')

        for entry in res_dict['ego_collision_list']:
            for k,v in entry.items():
                if k == 'other_id':
                    veh_name = res_dict['vehicle_dict'][v]
                    print(k,v, veh_name)
                else:
                    print(k,v)
        
        if exclude_collisions:
            raise ValueError("Collision encountered, so skipping this instance.")
    
    # Get reverse gear information to aid with speed sign.
    ego_control_dict =  extract_control_info(res_dict)
    
    # Extract ego's odometry and do goal association.
    ego_time = []
    ego_x       = [] # x coordinate in map frame (m)
    ego_y       = [] # y coordinate in map frame (m)
    ego_heading = [] # heading angle in map frame (rad)
    ego_v       = [] # estimated longitudinal velocity (m/s)
    ego_yawrate = [] # estimated angular velocity of vehicle frame wrt map frame (rad/s)

    for i, entry in enumerate(res_dict['ego_odometry_list']):
        time = entry['time']
        position = entry['position']

        ego_time.append(time)
        ego_x.append(position[0])
        ego_y.append(position[1])
        
        ehead = entry['orientation'][0]
        vx = entry['linear_velocity'][0]
        vy = entry['linear_velocity'][1]
        # This is super noisy...
        #vhead = np.arctan2(vy,vx) 
        #ehead_vec = np.array([np.cos(ehead), np.sin(ehead)])
        #vhead_vec = np.array([np.cos(vhead), np.sin(vhead)])
        #vel_sign = np.sign( np.dot(ehead_vec, vhead_vec) )
        
        if len(ego_v) > 0:
            v_long = get_longitudinal_velocity(time, vx, vy, ego_v[-1], ego_control_dict)
        else:
            v_long = get_longitudinal_velocity(time, vx, vy, None, ego_control_dict)
        
        ego_heading.append(ehead)
        ego_v.append( v_long )
        ego_yawrate.append(entry['angular_velocity'][2])
        # TODO: Could add ego_ctrl_hist for acceleration, although it's noisy. 

    inds_moving = [ n for n,ev in enumerate(ego_v) if np.abs(ev) > min_vel_thresh]
    start_ind = inds_moving[0] if prune_start else 0
    end_ind = inds_moving[-1] if prune_end else len(ego_time)-1

    # Goal selection
    final_position = np.array([ ego_x[end_ind], ego_y[end_ind] ])
    goal_ind = np.argmin( np.sum(np.square(goals[:,:2] - final_position), axis=1) )

    ego_intent = [-1 if t < intention_time else goal_ind for t in ego_time]
    switch_ind = [n for n,intent in enumerate(ego_intent) if intent > -1][0]
    ego_trajectory = np.column_stack((ego_time, ego_x, ego_y, ego_heading, ego_v, ego_yawrate, ego_intent))
    
    return ego_trajectory, start_ind, switch_ind, end_ind, goal_ind


def interpolate_ego_trajectory(ego_trajectory, t_interp, switch_ind, include_intent = False):
    x_interp = np.interp(t_interp, ego_trajectory[:,0], ego_trajectory[:,1])
    y_interp = np.interp(t_interp, ego_trajectory[:,0], ego_trajectory[:,2]) 
    heading_interp = np.interp(t_interp, ego_trajectory[:,0], np.unwrap(ego_trajectory[:,3]))
    v_interp = np.interp(t_interp, ego_trajectory[:,0], ego_trajectory[:,4])
    yawrate_interp = np.interp(t_interp, ego_trajectory[:,0], ego_trajectory[:,5])
    
    if include_intent:
        if np.max(t_interp) >= ego_trajectory[switch_ind,0]:
            # if any portion of the snippet has goal determined,
            # treat the entire snippet as if the goal is known.
            intent = ego_trajectory[switch_ind, 6] * np.ones(x_interp.shape)
        else:
            intent = -1 * np.ones(x_interp.shape)
            
        return np.column_stack((x_interp, y_interp, heading_interp, v_interp, yawrate_interp, intent))
    else:
        return np.column_stack((x_interp, y_interp, heading_interp, v_interp, yawrate_interp))
    
def get_ego_trajectory_prediction_snippets(ego_trajectory, start_ind, switch_ind, end_ind, goal_ind, goals,\
                                           Nhist=5, Npred=20, Nskip=5, dt=0.1, ego_frame=False):
    features = []; labels = []; goal_snpts = []
    
    t_range_start = ego_trajectory[start_ind, 0] + Nhist * dt
    t_range_end   = ego_trajectory[end_ind, 0] - Npred * dt
    t_skip = Nskip * dt
    
    for t_snippet in np.arange(t_range_start, t_range_end + 0.5 * t_skip, t_skip):
        t_hist = [x*dt + t_snippet for x in range(-Nhist + 1, 1)] # -N_hist + 1, ... , 0 -> N_hist steps
        t_pred = [x*dt + t_snippet for x in range(1, Npred+1)] # 1, ..., N_pred -> N_pred steps
    
        feature = interpolate_ego_trajectory(ego_trajectory, t_hist, switch_ind, include_intent=False)
        label   = interpolate_ego_trajectory(ego_trajectory, t_pred, switch_ind, include_intent=True)
        features.append(feature)
        labels.append(label)
        goal_snpts.append(goals.copy())
        
    goal_snpts = np.array(goal_snpts)

    # Transform all snippets into ego frame, if ego_frame=True
    features_global = copy.deepcopy(features)
    labels_global   = copy.deepcopy(labels)
    if ego_frame:
        for id_snpt in range(len(features)):
            current = features[id_snpt][-1, :].copy()
            th = current[2]
            R = np.array([[ np.cos(th), np.sin(th)], \
                          [-np.sin(th), np.cos(th)]])
            curr_position = current[:2]

            for id_f in range(Nhist):
                features[id_snpt][id_f, :2] = R @ (features[id_snpt][id_f, :2] - curr_position)
                features[id_snpt][id_f, 2] = fix_angle(features[id_snpt][id_f,2] - th)
                
            for id_l in range(Npred):
                labels[id_snpt][id_l, :2] = R @ (labels[id_snpt][id_l, :2] - curr_position)
                labels[id_snpt][id_l, 2] = fix_angle(labels[id_snpt][id_l, 2] - th)
            
            for id_g in range(len(goals)):
                goal_snpts[id_snpt][id_g, :2] = R @ (goal_snpts[id_snpt][id_g, :2] - curr_position)

    return np.array(features), np.array(features_global), np.array(labels), np.array(labels_global), np.array(goal_snpts)